"""Deterministic benchmark metrics.

Every function here is pure: same inputs -> same outputs, no network, no clock, no
provider SDK. A metric that cannot be computed returns ``None`` rather than a
misleading zero -- "not measured" and "measured zero" must stay distinguishable in the
scorecard (see benchmark/STANDARD.md, "算不出来的一律 null，不记 0").

Nothing in this module produces a single composite score. Search, fetch and end-to-end
metrics are reported side by side on purpose.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --------------------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------------------

_TRACKING_PREFIXES = ("utm_", "gclid", "fbclid", "mc_cid", "mc_eid", "ref_", "yclid")


def normalize_url(url: str) -> str:
    """Normalise a URL for cross-provider comparison.

    Two providers almost never spell the same page identically: http vs https, a
    trailing slash, ``www.``, a fragment, or a tracking parameter. Comparing raw strings
    understates overlap and inflates "unique" contributions.

    Deliberately conservative: the path is kept verbatim (case matters on many servers)
    and no redirect resolution happens here.
    """
    if not url:
        return ""
    raw = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "http://" + raw
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith(_TRACKING_PREFIXES)
    ]
    return urlunsplit(("https", host, path, urlencode(query), ""))


def domain_of(url: str) -> str:
    host = (urlsplit(normalize_url(url)).hostname or "").lower()
    return host


# --------------------------------------------------------------------------------------
# Search metrics
# --------------------------------------------------------------------------------------


@dataclass
class SearchResult:
    """One result as a provider returned it."""

    url: str
    rank: int
    title: str = ""
    snippet: str = ""


@dataclass
class GoldLabel:
    """Human-verified expectations for one search case."""

    relevant: dict[str, int] = field(default_factory=dict)  # normalized url -> relevance 1..3
    official: set[str] = field(default_factory=set)  # normalized urls considered official
    required_entity_ids: tuple[str, ...] = ()
    forbidden: frozenset[str] = frozenset()

    @classmethod
    def from_case(cls, case: dict) -> "GoldLabel":
        gold = case.get("gold_label") or {}
        relevant: dict[str, int] = {}
        official: set[str] = set()
        for item in gold.get("relevant_urls") or []:
            key = normalize_url(item["url"])
            relevant[key] = int(item.get("relevance", 1))
            if item.get("is_official"):
                official.add(key)
        return cls(
            relevant=relevant,
            official=official,
            required_entity_ids=tuple(gold.get("required_entity_ids") or []),
            forbidden=frozenset(normalize_url(u) for u in gold.get("forbidden_urls") or []),
        )


def _hit(results: Sequence[SearchResult], gold: GoldLabel) -> list[bool]:
    return [normalize_url(r.url) in gold.relevant for r in results]


def precision_at_k(results: Sequence[SearchResult], gold: GoldLabel, k: int) -> float | None:
    """Share of the top-k slots that are relevant.

    Denominator is k (not the number returned) so a provider that returns 3 results for
    a top-10 request is not rewarded for abstaining.
    """
    if k <= 0:
        return None
    top = results[:k]
    if not top:
        return 0.0
    return sum(1 for r in top if normalize_url(r.url) in gold.relevant) / k


def recall_at_k(results: Sequence[SearchResult], gold: GoldLabel, k: int) -> float | None:
    if not gold.relevant:
        return None
    top = results[:k]
    found = {normalize_url(r.url) for r in top} & set(gold.relevant)
    return len(found) / len(gold.relevant)


def reciprocal_rank(results: Sequence[SearchResult], gold: GoldLabel) -> float | None:
    if not gold.relevant:
        return None
    for i, r in enumerate(results, start=1):
        if normalize_url(r.url) in gold.relevant:
            return 1.0 / i
    return 0.0


def _dcg(gains: Iterable[float]) -> float:
    return sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(gains, start=1))


def ndcg_at_k(results: Sequence[SearchResult], gold: GoldLabel, k: int) -> float | None:
    """Graded nDCG. Relevance 3 = authoritative, 2 = useful, 1 = tangential."""
    if not gold.relevant or k <= 0:
        return None
    gains = []
    for r in results[:k]:
        key = normalize_url(r.url)
        gains.append(float(gold.relevant.get(key, 0)))
    ideal = sorted(gold.relevant.values(), reverse=True)[:k]
    denom = _dcg(ideal)
    if denom == 0:
        return None
    return _dcg(gains) / denom


def official_source_rate(results: Sequence[SearchResult], gold: GoldLabel) -> float | None:
    """Share of returned URLs that belong to an authoritative source.

    Computed against the gold 'official' set, so it measures relevance, not just TLD
    heuristics.
    """
    if not results or not gold.official:
        return None
    return sum(1 for r in results if normalize_url(r.url) in gold.official) / len(results)


def domain_diversity(results: Sequence[SearchResult]) -> float | None:
    if not results:
        return None
    return len({domain_of(r.url) for r in results}) / len(results)


def duplicate_rate(results: Sequence[SearchResult]) -> float | None:
    if not results:
        return None
    keys = [normalize_url(r.url) for r in results]
    return 1.0 - (len(set(keys)) / len(keys))


def market_language_accuracy(
    results: Sequence[SearchResult], market: str, language: str
) -> float | None:
    """Share of results whose host TLD or URL language marker matches the case market.

    A coarse signal only: it catches a provider silently answering with the wrong
    country's index, which is a common failure when region parameters are dropped.
    """
    if not results:
        return None
    ok = 0
    for r in results:
        host = domain_of(r.url)
        tld = host.rsplit(".", 1)[-1] if "." in host else ""
        if market and tld == market:
            ok += 1
        elif language and (f"/{language}/" in urlsplit(normalize_url(r.url)).path):
            ok += 1
    return ok / len(results)


def freshness_pass_rate(
    results: Sequence[SearchResult],
    gold: GoldLabel,
    published: dict[str, int | None],
    max_age_days: int | None,
) -> float | None:
    """Share of relevant hits whose known publication age is within budget.

    ``published`` maps normalized url -> age in days (None = unknown). Unknown ages are
    excluded from the denominator rather than counted as failures.
    """
    if max_age_days is None:
        return None
    known = [
        (normalize_url(r.url), published.get(normalize_url(r.url)))
        for r in results
        if normalize_url(r.url) in gold.relevant
    ]
    known = [(u, a) for u, a in known if a is not None]
    if not known:
        return None
    return sum(1 for _, a in known if a <= max_age_days) / len(known)


def unique_recall(
    results_by_provider: dict[str, Sequence[SearchResult]], gold: GoldLabel
) -> dict[str, float | None]:
    """For each provider, share of gold URLs that *only* it found.

    This is the metric that decides whether a second provider earns its place: a
    provider can be much worse on average and still be valuable if it alone finds the
    authoritative page.
    """
    if not gold.relevant:
        return {p: None for p in results_by_provider}
    found: dict[str, set[str]] = {
        p: {normalize_url(r.url) for r in rs} & set(gold.relevant)
        for p, rs in results_by_provider.items()
    }
    out: dict[str, float | None] = {}
    for provider, urls in found.items():
        others: set[str] = set()
        for other, other_urls in found.items():
            if other != provider:
                others |= other_urls
        unique = urls - others
        out[provider] = len(unique) / len(gold.relevant)
    return out


def marginal_contribution(
    results_by_provider: dict[str, Sequence[SearchResult]], gold: GoldLabel
) -> dict[str, float | None]:
    """Gold URLs added when this provider joins the union of all the others.

    Unlike ``unique_recall`` this is measured against the full ensemble, which is what
    an ablation test removes one provider at a time to estimate.
    """
    if not gold.relevant:
        return {p: None for p in results_by_provider}
    found = {
        p: {normalize_url(r.url) for r in rs} & set(gold.relevant)
        for p, rs in results_by_provider.items()
    }
    total = set().union(*found.values()) if found else set()
    out: dict[str, float | None] = {}
    for provider, urls in found.items():
        others: set[str] = set()
        for other, other_urls in found.items():
            if other != provider:
                others |= other_urls
        out[provider] = len(total - others) / len(gold.relevant)
    return out


# --------------------------------------------------------------------------------------
# Fetch metrics
# --------------------------------------------------------------------------------------


@dataclass
class FetchResult:
    """What a reader returned for one URL."""

    url: str
    ok: bool
    text: str = ""
    html: str = ""
    links: tuple[str, ...] = ()
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    persons: tuple[tuple[str, str], ...] = ()  # (name, role)
    status: int | None = None
    error_code: str = ""


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def extract_emails(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _EMAIL_RE.finditer(text or "")})


def extract_phones(text: str) -> list[str]:
    out = set()
    for m in _PHONE_RE.finditer(text or ""):
        digits = re.sub(r"\D", "", m.group(0))
        if 7 <= len(digits) <= 15:
            out.add(digits)
    return sorted(out)


def _set_prf(predicted: Iterable[str], expected: Iterable[str]) -> tuple[float | None, float | None]:
    p = set(predicted)
    e = set(expected)
    precision = (len(p & e) / len(p)) if p else None
    recall = (len(p & e) / len(e)) if e else None
    return precision, recall


def fetch_case_scores(result: FetchResult, case: dict) -> dict:
    """Score one fetch case. Returns raw per-case signals used by the aggregates."""
    gold = case.get("gold_label") or {}
    expected_outcome = case.get("expected_outcome", "content")
    text = result.text or ""
    html = result.html or ""
    haystack = f"{text}\n{html}"

    markers = [m for m in gold.get("expected_text_markers") or [] if m]
    markers_found = [m for m in markers if m.lower() in haystack.lower()]
    text_coverage = (len(markers_found) / len(markers)) if markers else None

    min_chars = gold.get("min_text_chars")
    if min_chars is not None:
        completeness = min(1.0, len(text) / min_chars) if min_chars else 1.0
    else:
        completeness = None

    expected_links = [link for link in gold.get("expected_links") or [] if link]
    link_retention = None
    if expected_links:
        present = sum(1 for link in expected_links if link.lower() in haystack.lower())
        link_retention = present / len(expected_links)

    mailtos = [link for link in expected_links if link.lower().startswith("mailto:")]
    mailto_retention = None
    if mailtos:
        present = sum(1 for link in mailtos if link.lower() in haystack.lower())
        mailto_retention = present / len(mailtos)

    email_p, email_r = _set_prf(
        extract_emails(haystack), [e.lower() for e in gold.get("expected_emails") or []]
    )
    phone_p, phone_r = _set_prf(
        extract_phones(haystack),
        [re.sub(r"\D", "", p) for p in gold.get("expected_phones") or []],
    )

    expected_persons = {(p.get("name", ""), p.get("role", "")) for p in gold.get("expected_persons") or []}
    person_acc = None
    if expected_persons:
        got = set(result.persons)
        person_acc = len(got & expected_persons) / len(expected_persons)

    forbidden = [s for s in gold.get("forbidden_strings") or [] if s]
    blocked_marker = any(s.lower() in text.lower() for s in forbidden)

    shell = False
    if expected_outcome == "content":
        # Claimed success, but what came back is chrome: too little text and none of the
        # phrases that would prove the real content was read.
        shell = result.ok and len(text.strip()) < 200 and (text_coverage or 0) < 0.5
    else:
        # A no_content case (login wall, 404, empty shell): *any* claimed success is a
        # false success. A returned login page counts even when the wall text is short,
        # and ``blocked_marker`` makes that case explicit rather than luck-dependent on
        # a 200-character threshold.
        shell = result.ok and (len(text.strip()) >= 200 or blocked_marker)

    return {
        "case_id": case["case_id"],
        "task_type": case.get("task_type"),
        "expected_outcome": expected_outcome,
        "ok": bool(result.ok),
        "blocked_marker": blocked_marker,
        "text_chars": len(text.strip()),
        "text_coverage": text_coverage,
        "content_completeness": completeness,
        "link_retention": link_retention,
        "mailto_retention": mailto_retention,
        "email_precision": email_p,
        "email_recall": email_r,
        "phone_precision": phone_p,
        "phone_recall": phone_r,
        "contact_person_accuracy": person_acc,
        "shell_page": shell,
        "requires_js": bool(gold.get("requires_js")),
        "is_pdf": case.get("task_type") in ("pdf_text", "pdf_scanned"),
    }


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def aggregate_fetch(per_case: Sequence[dict]) -> dict:
    """Roll per-case fetch signals up into the scorecard metric block."""
    if not per_case:
        return {k: None for k in (
            "http_success_rate", "useful_content_rate", "content_completeness",
            "text_coverage", "structured_extraction_accuracy", "email_precision",
            "email_recall", "phone_precision", "phone_recall", "contact_person_accuracy",
            "link_retention", "mailto_retention", "js_coverage", "pdf_coverage",
            "shell_page_false_success_rate",
        )}
    content_cases = [c for c in per_case if c["expected_outcome"] == "content"]
    no_content = [c for c in per_case if c["expected_outcome"] == "no_content"]
    return {
        "http_success_rate": _mean(1.0 if c["ok"] else 0.0 for c in per_case),
        "useful_content_rate": _mean(
            1.0 if (c["ok"] and not c["shell_page"]) else 0.0 for c in content_cases
        ),
        "content_completeness": _mean(c["content_completeness"] for c in content_cases),
        "text_coverage": _mean(c["text_coverage"] for c in content_cases),
        "structured_extraction_accuracy": _mean(
            _mean([c["email_recall"], c["phone_recall"], c["contact_person_accuracy"]])
            for c in content_cases
        ),
        "email_precision": _mean(c["email_precision"] for c in content_cases),
        "email_recall": _mean(c["email_recall"] for c in content_cases),
        "phone_precision": _mean(c["phone_precision"] for c in content_cases),
        "phone_recall": _mean(c["phone_recall"] for c in content_cases),
        "contact_person_accuracy": _mean(c["contact_person_accuracy"] for c in content_cases),
        "link_retention": _mean(c["link_retention"] for c in content_cases),
        "mailto_retention": _mean(c["mailto_retention"] for c in content_cases),
        "js_coverage": _mean(
            1.0 if (c["ok"] and not c["shell_page"]) else 0.0
            for c in content_cases
            if c["requires_js"]
        ),
        "pdf_coverage": _mean(
            1.0 if (c["ok"] and not c["shell_page"]) else 0.0
            for c in content_cases
            if c["is_pdf"]
        ),
        "shell_page_false_success_rate": _mean(1.0 if c["shell_page"] else 0.0 for c in no_content),
    }


# --------------------------------------------------------------------------------------
# End-to-end metrics
# --------------------------------------------------------------------------------------


@dataclass
class DeliveredEntity:
    entity_id: str
    name: str
    official_url: str | None = None
    contacts: tuple[dict, ...] = ()  # {"kind","value","evidence_url","person","role"}


def _contacts_key(c: dict) -> str:
    return f"{c.get('kind')}:{str(c.get('value', '')).strip().lower()}"


def e2e_scores(delivered: Sequence[DeliveredEntity], case: dict) -> dict:
    """Score one end-to-end delivery against its gold label.

    Entity matching is by id when the pipeline emits ids, else by normalized official
    URL or normalized name. Contacts must carry an evidence URL to count as verified --
    an address with no page behind it is a liability, not a result.
    """
    gold = case.get("gold_label") or {}
    gold_entities = {e["entity_id"]: e for e in gold.get("entities") or []}
    gold_contacts = [c for c in gold.get("contacts") or []]

    def entity_key(e: DeliveredEntity) -> str | None:
        """Match a delivered entity to a gold entity: id, then official URL, then name.

        Attribute access, not ``dict.get``: the documented input is
        :class:`DeliveredEntity`. A dict-shaped delivery is converted by
        ``cli._entities`` before it gets here, so there is exactly one shape to handle.
        """
        if e.entity_id in gold_entities:
            return e.entity_id
        if e.official_url:
            for gid, g in gold_entities.items():
                if g.get("official_url") and normalize_url(g["official_url"]) == normalize_url(
                    e.official_url
                ):
                    return gid
        name = (e.name or "").strip().lower()
        for gid, g in gold_entities.items():
            names = [g.get("canonical_name", "")] + list(g.get("aliases") or [])
            if name and name in {n.strip().lower() for n in names if n}:
                return gid
        return None

    matched_ids = {entity_key(d) for d in delivered}
    matched_ids.discard(None)
    entity_precision = (len(matched_ids) / len(delivered)) if delivered else None
    entity_recall = (len(matched_ids & set(gold_entities)) / len(gold_entities)) if gold_entities else None
    false_positive_rate = (
        (len(delivered) - len(matched_ids)) / len(delivered) if delivered else None
    )
    duplicate_rate = (
        1.0 - len(matched_ids) / len(delivered) if delivered else None
    )

    delivered_contacts = [
        (entity_key(d), dict(c)) for d in delivered for c in d.contacts if c.get("evidence_url")
    ]
    gold_keys = {(c["entity_id"], _contacts_key(c)) for c in gold_contacts}
    got_keys = {(eid, _contacts_key(c)) for eid, c in delivered_contacts if eid}
    contact_precision = (len(got_keys & gold_keys) / len(got_keys)) if got_keys else None
    contact_recall = (len(got_keys & gold_keys) / len(gold_keys)) if gold_keys else None

    attribution_ok = [
        1.0 if eid == _gold_entity_for_contact(c, gold_contacts) else 0.0
        for eid, c in delivered_contacts
        if eid
    ]
    attribution = _mean(attribution_ok)

    attachment_ok = [
        1.0
        if any(
            normalize_url(c.get("evidence_url", "")) == normalize_url(g["evidence_url"])
            for g in gold_contacts
            if _contacts_key(g) == _contacts_key(c)
        )
        else 0.0
        for _, c in delivered_contacts
    ]
    source_attribution = _mean(attachment_ok)

    with_evidence = sum(
        1
        for d in delivered
        for c in d.contacts
        if c.get("evidence_url") and c.get("value")
    )
    total_contacts = sum(len(d.contacts) for d in delivered)
    evidence_completeness = (with_evidence / total_contacts) if total_contacts else None

    acceptance = case.get("acceptance") or {}
    completed = True
    if acceptance.get("min_entity_recall") is not None and entity_recall is not None:
        completed &= entity_recall >= acceptance["min_entity_recall"]
    if acceptance.get("min_verified_contacts") is not None:
        completed &= len(got_keys & gold_keys) >= acceptance["min_verified_contacts"]
    if acceptance.get("evidence_required") and evidence_completeness is not None:
        # No claimed contact means nothing to substantiate: the rule is vacuously
        # satisfied, not failed. Folding ``None`` into 0 made every entity-only case
        # impossible to complete, which is a property of the scorer, not of the run.
        completed &= evidence_completeness >= 1.0
    if acceptance.get("max_false_positive_rate") is not None and false_positive_rate is not None:
        completed &= false_positive_rate <= acceptance["max_false_positive_rate"]

    return {
        "case_id": case["case_id"],
        "entity_precision": entity_precision,
        "entity_recall": entity_recall,
        "verified_contact_precision": contact_precision,
        "verified_contact_recall": contact_recall,
        "correct_entity_attribution": attribution,
        "source_attribution_accuracy": source_attribution,
        "evidence_completeness": evidence_completeness,
        "false_positive_rate": false_positive_rate,
        "duplicate_entity_rate": duplicate_rate,
        "task_completed": 1.0 if completed else 0.0,
        "verified_entities": len(matched_ids),
        "verified_contacts": len(got_keys & gold_keys),
    }


def _gold_entity_for_contact(contact: dict, gold_contacts: Sequence[dict]) -> str | None:
    key = _contacts_key(contact)
    for g in gold_contacts:
        if _contacts_key(g) == key:
            return g.get("entity_id")
    return None


def aggregate_e2e(per_case: Sequence[dict]) -> dict:
    if not per_case:
        return {k: None for k in (
            "entity_precision", "entity_recall", "verified_contact_precision",
            "verified_contact_recall", "correct_entity_attribution",
            "source_attribution_accuracy", "evidence_completeness",
            "false_positive_rate", "duplicate_entity_rate", "task_completion_rate",
        )}
    return {
        "entity_precision": _mean(c["entity_precision"] for c in per_case),
        "entity_recall": _mean(c["entity_recall"] for c in per_case),
        "verified_contact_precision": _mean(c["verified_contact_precision"] for c in per_case),
        "verified_contact_recall": _mean(c["verified_contact_recall"] for c in per_case),
        "correct_entity_attribution": _mean(c["correct_entity_attribution"] for c in per_case),
        "source_attribution_accuracy": _mean(c["source_attribution_accuracy"] for c in per_case),
        "evidence_completeness": _mean(c["evidence_completeness"] for c in per_case),
        "false_positive_rate": _mean(c["false_positive_rate"] for c in per_case),
        "duplicate_entity_rate": _mean(c["duplicate_entity_rate"] for c in per_case),
        "task_completion_rate": _mean(c["task_completed"] for c in per_case),
    }


# --------------------------------------------------------------------------------------
# Cost / latency helpers
# --------------------------------------------------------------------------------------


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank percentile; returns None for an empty sample rather than 0."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return float(ordered[rank - 1])


def cost_per_result(total_cost_usd: float, relevant_count: int, unit: int = 100) -> float | None:
    if relevant_count <= 0:
        return None
    return total_cost_usd / relevant_count * unit


def ablation(
    ensemble_found: dict[str, set[str]], gold_total: int
) -> dict[str, dict]:
    """Provider-ablation report: recall of the ensemble with each provider removed.

    ``ensemble_found`` maps provider -> set of gold ids it found. Returns, per provider,
    the recall drop caused by removing it (its true marginal value) and whether the
    remaining ensemble still covers the gold set.
    """
    providers = list(ensemble_found)
    full = set().union(*ensemble_found.values()) if ensemble_found else set()
    full_recall = len(full) / gold_total if gold_total else None
    out: dict[str, dict] = {}
    for p in providers:
        rest: set[str] = set()
        for q, urls in ensemble_found.items():
            if q != p:
                rest |= urls
        rest_recall = len(rest) / gold_total if gold_total else None
        out[p] = {
            "ensemble_recall": full_recall,
            "recall_without_provider": rest_recall,
            "recall_drop": (full_recall - rest_recall) if (full_recall is not None and rest_recall is not None) else None,
        }
    return out


def ablate_cases(cases: Sequence[dict], recorded) -> dict[str, dict]:
    """Ensemble ablation over many cases.

    ``recorded`` is duck-typed: anything with ``get(provider, case_id) -> dict | None``
    where the body is a recorded search response understood by
    :func:`benchmark.runner.normalize.search_rows`.

    For each provider, accumulate (a) recall of the whole ensemble on these cases and
    (b) recall after removing that provider. The difference is the provider's true
    marginal value -- which is not the same as its standalone score, and is the number
    that decides whether a second or third provider is worth paying for.
    """
    from .normalize import search_rows  # local import keeps metrics dependency-free

    providers = sorted({f.parent.name for f in recorded.root.glob("*/*.json")})
    gold_total = 0
    ensemble_hit = 0
    without_hit = {p: 0 for p in providers}

    for case in cases:
        gold = set(GoldLabel.from_case(case).relevant)
        if not gold:
            continue
        gold_total += len(gold)
        found: dict[str, set[str]] = {}
        for provider in providers:
            body = recorded.get(provider, case["case_id"])
            if body is None:
                found[provider] = set()
                continue
            found[provider] = {normalize_url(r["url"]) for r in search_rows(provider, body)} & gold
        union: set[str] = set().union(*found.values()) if found else set()
        ensemble_hit += len(union)
        for provider in providers:
            rest: set[str] = set()
            for other, urls in found.items():
                if other != provider:
                    rest |= urls
            without_hit[provider] += len(rest)

    if not gold_total:
        return {}
    full = ensemble_hit / gold_total
    return {
        provider: {
            "ensemble_recall": full,
            "recall_without_provider": without_hit[provider] / gold_total,
            "recall_drop": full - without_hit[provider] / gold_total,
        }
        for provider in providers
    }


def pareto_front(points: Sequence[dict], quality_key: str, cost_key: str) -> list[int]:
    """Indices of points not dominated on (maximise quality, minimise cost)."""
    front: list[int] = []
    for i, a in enumerate(points):
        qa, ca = a.get(quality_key), a.get(cost_key)
        if qa is None or ca is None:
            continue
        dominated = False
        for j, b in enumerate(points):
            if i == j:
                continue
            qb, cb = b.get(quality_key), b.get(cost_key)
            if qb is None or cb is None:
                continue
            if qb >= qa and cb <= ca and (qb > qa or cb < ca):
                dominated = True
                break
        if not dominated:
            front.append(i)
    return front


def error_histogram(errors: Iterable[str]) -> dict[str, int]:
    return dict(Counter(errors))
