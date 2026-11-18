"""Blind judge: payload construction, verdict validation and calibration.

Two rules shape this module.

**The judge never sees who produced what.** Provider names, endpoints, latency and
cost are stripped before the payload is built; candidates are relabelled
``candidate_a``, ``candidate_b``, ... and the label mapping is written to a separate
sealed file that is not part of the judge request. A judge that can infer the vendor
from a domain, a model name or a rank order is not blind.

**A judge is only usable after calibration.** Scores from an uncalibrated judge are
reported as uncalibrated, never as ground truth. Calibration compares the judge's
binary decisions with human labels on the same cases and reports agreement,
false-positive rate and false-negative rate separately -- a judge that never says
"not relevant" can score well on agreement while being useless.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

JUDGE_PROMPT_VERSION = "marketops.judge.v1"
JUDGE_SCHEMA_VERSION = "marketops.benchmark.judge.v1"


class JudgeError(ValueError):
    """Raised when a verdict is malformed or cannot be trusted."""


# --------------------------------------------------------------------------------------
# Blind payloads
# --------------------------------------------------------------------------------------


def _seed(*parts: str) -> int:
    digest = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def blind_labels(providers: Sequence[str], *, salt: str) -> dict[str, str]:
    """Map real provider names to ``candidate_x`` labels, order varying per salt.

    Order matters: if the same provider is always ``candidate_a``, position becomes a
    hidden identity signal across cases. The shuffle is seeded so a replay reproduces
    the same labels exactly.
    """
    names = list(providers)
    random.Random(_seed(salt, *names)).shuffle(names)
    return {name: f"candidate_{chr(ord('a') + i)}" for i, name in enumerate(names)}


def build_search_payload(case: dict, results_by_provider: dict[str, Sequence[dict]], *,
                         salt: str) -> tuple[dict, dict[str, str]]:
    """Build the judge's view of one search case. Returns (payload, label_map)."""
    labels = blind_labels(list(results_by_provider), salt=f"{salt}:{case['case_id']}")
    candidates = []
    for provider, results in results_by_provider.items():
        candidates.append(
            {
                "candidate": labels[provider],
                "results": [
                    {
                        "rank": r.get("rank", i + 1),
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": (r.get("snippet") or "")[:400],
                    }
                    for i, r in enumerate(results)
                ],
            }
        )
    payload = {
        "prompt_version": JUDGE_PROMPT_VERSION,
        "layer": "search",
        "case": {
            "case_id": case["case_id"],
            "market": case.get("market"),
            "language": case.get("language"),
            "query": case.get("query"),
            "task_type": case.get("task_type"),
            "top_k": case.get("top_k", 10),
            "notes": case.get("notes", ""),
        },
        "candidates": candidates,
        "instructions": [
            "Score each candidate on the seven dimensions. 0 = useless, 1 = perfect.",
            "Every score needs at least one verbatim quote from the payload as evidence.",
            "Do not guess a candidate's provider; if you can, say so and abstain.",
            "If the payload does not let you judge a dimension, set score to null and abstain.",
        ],
    }
    return payload, labels


def build_fetch_payload(case: dict, texts_by_reader: dict[str, str], *, salt: str,
                        max_chars: int = 6000) -> tuple[dict, dict[str, str]]:
    labels = blind_labels(list(texts_by_reader), salt=f"{salt}:{case['case_id']}")
    candidates = [
        {
            "candidate": labels[reader],
            "extracted_text": text[:max_chars],
            "truncated": len(text) > max_chars,
        }
        for reader, text in texts_by_reader.items()
    ]
    payload = {
        "prompt_version": JUDGE_PROMPT_VERSION,
        "layer": "fetch",
        "case": {
            "case_id": case["case_id"],
            "task_type": case.get("task_type"),
            "url": case.get("url"),
            "expected_outcome": case.get("expected_outcome"),
            "notes": case.get("notes", ""),
        },
        "candidates": candidates,
        "instructions": [
            "Judge whether the extracted text is the page's real content or a shell.",
            "Quote the exact span that decides it; if none exists, abstain.",
        ],
    }
    return payload, labels


def build_e2e_payload(case: dict, deliveries: dict[str, dict], *, salt: str) -> tuple[dict, dict[str, str]]:
    labels = blind_labels(list(deliveries), salt=f"{salt}:{case['case_id']}")
    candidates = [
        {"candidate": labels[name], "delivery": delivery}
        for name, delivery in deliveries.items()
    ]
    payload = {
        "prompt_version": JUDGE_PROMPT_VERSION,
        "layer": "e2e",
        "case": {
            "case_id": case["case_id"],
            "scenario": case.get("scenario"),
            "brief": case.get("brief"),
            "target_entities": case.get("target_entities"),
        },
        "candidates": candidates,
        "instructions": [
            "Judge the delivered business result, not the intermediate API responses.",
            "Every claimed contact must carry a page where it actually appears as stated.",
        ],
    }
    return payload, labels


def write_sealed_mapping(mapping: dict[str, dict[str, str]], path: str | Path) -> Path:
    """Persist label maps outside the judge request.

    Sealed means: it exists for the analyst after scoring, and the judge request never
    references it. It is written with sorted keys so a replay produces byte-identical
    output.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8", newline="\n")
    return p


# --------------------------------------------------------------------------------------
# Verdict validation
# --------------------------------------------------------------------------------------

DIMENSIONS = (
    "search_relevance",
    "source_authority",
    "content_completeness",
    "evidence_quality",
    "entity_attribution",
    "freshness",
    "final_answer_quality",
)


def validate_verdict(verdict: dict, payload: dict) -> None:
    """Structural checks that do not require a JSON-Schema library.

    Beyond field presence this enforces the two properties that make a judge verdict
    worth anything: evidence quotes must appear in the payload the judge was given,
    and a scored dimension must carry at least one quote.
    """
    if verdict.get("schema_version") != JUDGE_SCHEMA_VERSION:
        raise JudgeError(f"schema_version must be {JUDGE_SCHEMA_VERSION}")
    if verdict.get("case_id") != payload["case"]["case_id"]:
        raise JudgeError("verdict case_id does not match the payload")
    if verdict.get("layer") != payload["layer"]:
        raise JudgeError("verdict layer does not match the payload")
    if verdict.get("blind") is not True:
        raise JudgeError("a verdict from an unblinded payload cannot be used for ranking")

    haystack = json.dumps(payload, ensure_ascii=False)
    dims = verdict.get("dimensions") or {}
    for name in DIMENSIONS:
        dim = dims.get(name)
        if not isinstance(dim, dict):
            raise JudgeError(f"missing dimension {name}")
        score = dim.get("score")
        if score is not None and not (isinstance(score, (int, float)) and 0 <= score <= 1):
            raise JudgeError(f"{name}.score must be null or within [0, 1]")
        evidence = dim.get("evidence") or []
        if score is not None and not evidence:
            raise JudgeError(f"{name} is scored but cites no evidence")
        for item in evidence:
            quote = (item or {}).get("quote", "")
            if not quote:
                raise JudgeError(f"{name} has an empty evidence quote")
            if quote[:120] not in haystack:
                raise JudgeError(
                    f"{name} cites a quote that does not appear in the payload: {quote[:60]!r}"
                )
    if verdict.get("abstained") and not verdict.get("abstain_reason"):
        raise JudgeError("an abstention must state its reason")


def verdict_to_binary(verdict: dict, threshold: float = 0.6) -> bool | None:
    """Collapse a verdict to a usable/not-usable decision for calibration.

    Returns ``None`` when the verdict abstained -- abstentions are excluded from
    agreement rather than silently counted as "not usable", which would flatter a
    judge that abstains on everything it finds hard.
    """
    if verdict.get("abstained"):
        return None
    score = (verdict.get("overall") or {}).get("score")
    if score is None:
        return None
    return score >= threshold


# --------------------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------------------


@dataclass
class CalibrationReport:
    sample_size: int = 0
    abstentions: int = 0
    agreement: float | None = None
    false_positive_rate: float | None = None
    false_negative_rate: float | None = None
    mean_absolute_error: float | None = None
    per_dimension_mae: dict[str, float | None] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "sample_size": self.sample_size,
            "abstentions": self.abstentions,
            "agreement": self.agreement,
            "false_positive_rate": self.false_positive_rate,
            "false_negative_rate": self.false_negative_rate,
            "mean_absolute_error": self.mean_absolute_error,
            "per_dimension_mae": dict(sorted(self.per_dimension_mae.items())),
        }


def calibrate(
    verdicts: Iterable[dict],
    human_labels: dict[str, dict],
    *,
    threshold: float = 0.6,
) -> CalibrationReport:
    """Compare judge verdicts with human labels on the same cases.

    ``human_labels`` maps case_id -> {"overall": 0..1, "dimensions": {...}} where
    dimensions are optional. Cases missing from ``human_labels`` are skipped: an
    unlabelled case cannot calibrate anything.
    """
    tp = fp = tn = fn = 0
    abs_errors: list[float] = []
    per_dim: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    abstentions = 0
    used = 0

    for verdict in verdicts:
        case_id = verdict.get("case_id")
        human = human_labels.get(case_id)
        if not human:
            continue
        used += 1
        if verdict.get("abstained"):
            abstentions += 1
            continue
        judge_binary = verdict_to_binary(verdict, threshold)
        if judge_binary is None:
            abstentions += 1
            continue
        human_score = human.get("overall")
        human_binary = bool(human_score is not None and human_score >= threshold)
        if judge_binary and human_binary:
            tp += 1
        elif judge_binary and not human_binary:
            fp += 1
        elif not judge_binary and human_binary:
            fn += 1
        else:
            tn += 1

        judge_score = (verdict.get("overall") or {}).get("score")
        if judge_score is not None and human_score is not None:
            abs_errors.append(abs(judge_score - human_score))
        for name in DIMENSIONS:
            j = ((verdict.get("dimensions") or {}).get(name) or {}).get("score")
            h = (human.get("dimensions") or {}).get(name)
            if j is not None and h is not None:
                per_dim[name].append(abs(j - h))

    decided = tp + fp + tn + fn
    report = CalibrationReport(
        sample_size=used,
        abstentions=abstentions,
        agreement=((tp + tn) / decided) if decided else None,
        false_positive_rate=(fp / (fp + tn)) if (fp + tn) else None,
        false_negative_rate=(fn / (fn + tp)) if (fn + tp) else None,
        mean_absolute_error=(sum(abs_errors) / len(abs_errors)) if abs_errors else None,
    )
    report.per_dimension_mae = {
        d: (sum(v) / len(v)) if v else None for d, v in per_dim.items()
    }
    return report


def calibration_grade(report: CalibrationReport) -> str:
    """Name how much weight a run may put on its judge scores.

    Deliberately conservative. A judge that agrees less than 80% on a sample of at
    least 10 decided cases is reported as uncalibrated and its scores are advisory
    only.
    """
    if report.sample_size < 10 or report.agreement is None:
        return "insufficient_sample"
    if report.agreement < 0.80:
        return "uncalibrated"
    if report.false_positive_rate is not None and report.false_positive_rate > 0.20:
        return "uncalibrated"
    return "calibrated"


def load_verdicts(path: str | Path) -> list[dict]:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return [data]
    if not isinstance(data, list):
        raise JudgeError(f"{p.name}: expected one verdict or a list of verdicts")
    return data


def summaries(verdicts: Sequence[dict]) -> dict[str, Any]:
    """Aggregate judge output for the scorecard, without pretending it is truth."""
    scores = [
        (v.get("overall") or {}).get("score")
        for v in verdicts
        if not v.get("abstained")
    ]
    scores = [s for s in scores if s is not None]
    by_dim: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    for v in verdicts:
        if v.get("abstained"):
            continue
        for d in DIMENSIONS:
            s = ((v.get("dimensions") or {}).get(d) or {}).get("score")
            if s is not None:
                by_dim[d].append(s)
    return {
        "verdicts": len(verdicts),
        "abstentions": sum(1 for v in verdicts if v.get("abstained")),
        "mean_overall": (sum(scores) / len(scores)) if scores else None,
        "mean_by_dimension": {
            d: (sum(v) / len(v)) if v else None for d, v in sorted(by_dim.items())
        },
        "total_cost_usd": sum(
            ((v.get("usage") or {}).get("cost_usd") or 0.0) for v in verdicts
        ),
    }
