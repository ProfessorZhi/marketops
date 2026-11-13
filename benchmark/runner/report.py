"""Assemble scorecards and render them.

The scorecard is the only artefact a reader is meant to trust without reading the
runner. Three rules keep it honest:

* deterministic metrics and judge metrics live in separate blocks and are never
  averaged into one number;
* a metric that could not be computed is ``null``, never ``0``;
* ablation (what the ensemble loses without a provider) is a separate block, because
  it answers a different question than "who scored best".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import SCHEMA_VERSION
from .judge import CalibrationReport, calibration_grade, summaries
from .metrics import aggregate_e2e, aggregate_fetch, percentile

SCORECARD_VERSION = "marketops.benchmark.scorecard.v1"

SEARCH_METRIC_KEYS = (
    "precision_at_5", "precision_at_10", "recall_at_5", "recall_at_10", "mrr",
    "ndcg_at_10", "unique_recall", "official_source_rate", "domain_diversity",
    "duplicate_rate", "market_language_accuracy", "freshness_pass_rate",
)
FETCH_METRIC_KEYS = (
    "http_success_rate", "useful_content_rate", "content_completeness", "text_coverage",
    "structured_extraction_accuracy", "email_precision", "email_recall",
    "phone_precision", "phone_recall", "contact_person_accuracy", "link_retention",
    "mailto_retention", "js_coverage", "pdf_coverage", "shell_page_false_success_rate",
)
E2E_METRIC_KEYS = (
    "entity_precision", "entity_recall", "verified_contact_precision",
    "verified_contact_recall", "correct_entity_attribution", "source_attribution_accuracy",
    "evidence_completeness", "false_positive_rate", "duplicate_entity_rate",
    "task_completion_rate",
)


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


# --------------------------------------------------------------------------------------
# Arms
# --------------------------------------------------------------------------------------


@dataclass
class SearchArm:
    provider: str
    per_case: list[dict] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    costs_usd: list[float] = field(default_factory=list)
    relevant_found: int = 0
    gold_total: int = 0
    unique_found: int = 0
    failures: list[dict] = field(default_factory=list)

    def metrics(self) -> dict:
        out = {k: _round(_mean(c.get(k) for c in self.per_case)) for k in SEARCH_METRIC_KEYS}
        out["unique_recall"] = (
            _round(self.unique_found / self.gold_total) if self.gold_total else None
        )
        return out


@dataclass
class FetchArm:
    reader: str
    per_case: list[dict] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    costs_usd: list[float] = field(default_factory=list)
    useful: int = 0
    failures: list[dict] = field(default_factory=list)

    def metrics(self) -> dict:
        """Delegate to :func:`aggregate_fetch`.

        A per-case mean over ``FETCH_METRIC_KEYS`` looks right and is wrong: the
        per-case scorer emits raw signals (``ok``, ``shell_page``, ``requires_js``),
        not the rates. Mean-ing the keys left ``useful_content_rate``,
        ``shell_page_false_success_rate``, ``js_coverage`` and ``pdf_coverage``
        permanently null -- i.e. the scorecard could not report the two failures the
        benchmark exists to catch.
        """
        return {k: _round(v) for k, v in aggregate_fetch(self.per_case).items()}


@dataclass
class E2EArm:
    configuration: str
    per_case: list[dict] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    costs_usd: list[float] = field(default_factory=list)
    verified_entities: int = 0
    verified_contacts: int = 0
    failures: list[dict] = field(default_factory=list)

    def metrics(self) -> dict:
        """Delegate to :func:`aggregate_e2e` (the per-case key is ``task_completed``;
        mean-ing ``task_completion_rate`` over it returned ``null`` forever)."""
        return {k: _round(v) for k, v in aggregate_e2e(self.per_case).items()}


def _latency(values: Sequence[float]) -> dict:
    """p50/p95, or ``null`` when the arm produced no observation.

    An empty latency sample must not become ``0.0``: that reads as "instant" in the
    report, which is the opposite of "never measured".
    """
    return {
        "p50": _round(percentile(values, 50), 1),
        "p95": _round(percentile(values, 95), 1),
    }


# --------------------------------------------------------------------------------------
# Scorecard
# --------------------------------------------------------------------------------------


def build_scorecard(
    *,
    run_id: str,
    dataset_name: str,
    dataset_version: str,
    dataset_checksum: str,
    split: str,
    case_counts: dict[str, int],
    label_status: dict[str, int],
    mode: str,
    repeats: int,
    operator: str,
    marketops_version: str,
    git_commit: str,
    python_version: str,
    providers: dict[str, dict],
    search_arms: Sequence[SearchArm] = (),
    fetch_arms: Sequence[FetchArm] = (),
    e2e_arms: Sequence[E2EArm] = (),
    judge_provider: str = "none",
    judge_model: str = "",
    judge_blind: bool = True,
    calibration: CalibrationReport | None = None,
    started_at: str | None = None,
    notes: Sequence[str] = (),
) -> dict:
    cal = calibration or CalibrationReport()
    grade = calibration_grade(cal)
    all_notes = list(notes)
    all_notes.append(f"judge_calibration: {grade}")
    if judge_provider == "none":
        all_notes.append("judge: none -- deterministic metrics only")

    return {
        "schema_version": SCORECARD_VERSION,
        "run": {
            "run_id": run_id,
            "started_at": started_at or _now(),
            "finished_at": _now(),
            "mode": mode,
            "repeats": repeats,
            "operator": operator,
        },
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
            "checksum": dataset_checksum,
            "split": split,
            "case_counts": {
                "search": case_counts.get("search", 0),
                "fetch": case_counts.get("fetch", 0),
                "e2e": case_counts.get("e2e", 0),
            },
            "label_status": dict(label_status),
        },
        "environment": {
            "marketops_version": marketops_version,
            "git_commit": git_commit,
            "python_version": python_version,
            "network": "offline" if mode != "live" else "live",
            "providers": providers,
        },
        "search": [
            {
                "provider": arm.provider,
                "cases": len(arm.per_case),
                "metrics": arm.metrics(),
                "latency_ms": _latency(arm.latencies_ms),
                "cost": {
                    "total_usd": round(sum(arm.costs_usd), 6),
                    "per_100_relevant_results_usd": _round(
                        (sum(arm.costs_usd) / arm.relevant_found * 100)
                        if arm.relevant_found else None,
                        6,
                    ),
                },
                "failures": arm.failures,
            }
            for arm in search_arms
        ],
        "fetch": [
            {
                "reader": arm.reader,
                "cases": len(arm.per_case),
                "metrics": arm.metrics(),
                "latency_ms": _latency(arm.latencies_ms),
                "cost": {
                    "total_usd": round(sum(arm.costs_usd), 6),
                    "per_successful_page_usd": _round(
                        (sum(arm.costs_usd) / arm.useful) if arm.useful else None, 6
                    ),
                },
                "failures": arm.failures,
            }
            for arm in fetch_arms
        ],
        "e2e": [
            {
                "configuration": arm.configuration,
                "cases": len(arm.per_case),
                "metrics": arm.metrics(),
                "cost": {
                    "total_usd": round(sum(arm.costs_usd), 6),
                    "per_verified_entity_usd": _round(
                        (sum(arm.costs_usd) / arm.verified_entities)
                        if arm.verified_entities else None, 6
                    ),
                    "per_verified_contact_usd": _round(
                        (sum(arm.costs_usd) / arm.verified_contacts)
                        if arm.verified_contacts else None, 6
                    ),
                },
                "latency_ms": _latency(arm.latencies_ms),
                "failures": arm.failures,
            }
            for arm in e2e_arms
        ],
        "judge": {
            "provider": judge_provider,
            "model": judge_model,
            "blind": judge_blind,
            "calibration": {
                "sample_size": cal.sample_size,
                "agreement": _round(cal.agreement),
                "false_positive_rate": _round(cal.false_positive_rate),
                "false_negative_rate": _round(cal.false_negative_rate),
            },
        },
        "notes": all_notes,
    }


def write_scorecard(scorecard: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(scorecard, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return p


# --------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(_fmt(v) for v in row) + " |")
    return lines


def render_markdown(scorecard: dict) -> str:
    run = scorecard["run"]
    ds = scorecard["dataset"]
    env = scorecard["environment"]
    judge = scorecard["judge"]
    cal = judge["calibration"]

    out: list[str] = []
    out.append(f"# Benchmark run `{run['run_id']}`")
    out.append("")
    out.append(
        f"- mode: **{run['mode']}** · repeats: {run['repeats']} · operator: {run['operator']}"
    )
    out.append(
        f"- dataset: {ds['name']} v{ds['version']} ({ds['split']}), "
        f"cases search={ds['case_counts']['search']} fetch={ds['case_counts']['fetch']} "
        f"e2e={ds['case_counts']['e2e']}"
    )
    status = ", ".join(f"{k}={v}" for k, v in ds.get("label_status", {}).items()) or "unstated"
    out.append(f"- dataset checksum: `{ds['checksum']}`")
    out.append(f"- gold label status: **{status}**")
    if set(ds.get("label_status", {})) - {"human_verified"}:
        # Say it on the artefact people quote, not only in the docs.
        out.append(
            "- ⚠ gold labels are not `human_verified`: these numbers are not decision-grade."
        )
    out.append(
        f"- environment: marketops {env['marketops_version']} @ {env['git_commit']}, "
        f"python {env['python_version']}, network {env['network']}"
    )
    out.append("")

    if scorecard["search"]:
        out.append("## Search")
        out.append("")
        headers = ["provider", "cases"] + list(SEARCH_METRIC_KEYS[:6]) + ["p95 ms", "USD/100 rel."]
        rows = []
        for arm in scorecard["search"]:
            m = arm["metrics"]
            rows.append(
                [arm["provider"], arm["cases"]]
                + [m[k] for k in SEARCH_METRIC_KEYS[:6]]
                + [arm["latency_ms"]["p95"], arm["cost"]["per_100_relevant_results_usd"]]
            )
        out.extend(_table(headers, rows))
        out.append("")
        out.append("Other search metrics")
        out.append("")
        rest = list(SEARCH_METRIC_KEYS[6:])
        out.extend(_table(["provider"] + rest, [[a["provider"]] + [a["metrics"][k] for k in rest]
                                              for a in scorecard["search"]]))
        out.append("")

    if scorecard["fetch"]:
        out.append("## Fetch")
        out.append("")
        key5 = ["useful_content_rate", "text_coverage", "email_precision", "email_recall",
                "pdf_coverage"]
        out.extend(_table(
            ["reader", "cases"] + key5 + ["p95 ms", "USD/page"],
            [[a["reader"], a["cases"]] + [a["metrics"][k] for k in key5]
            + [a["latency_ms"]["p95"], a["cost"]["per_successful_page_usd"]]
            for a in scorecard["fetch"]],
        ))
        out.append("")

    if scorecard["e2e"]:
        out.append("## End to end")
        out.append("")
        keys = ["entity_recall", "verified_contact_precision", "verified_contact_recall",
                "evidence_completeness", "false_positive_rate", "task_completion_rate"]
        out.extend(_table(
            ["configuration", "cases"] + keys,
            [[a["configuration"], a["cases"]] + [a["metrics"][k] for k in keys]
            for a in scorecard["e2e"]],
        ))
        out.append("")

    out.append("## Judge")
    out.append("")
    out.append(f"- provider: {judge['provider']} · model: {judge['model'] or 'n/a'} "
               f"· blind: {judge['blind']}")
    out.append(f"- calibration sample: {cal['sample_size']}, "
               f"agreement {_fmt(cal['agreement'])}, FP {_fmt(cal['false_positive_rate'])}, "
               f"FN {_fmt(cal['false_negative_rate'])}")
    out.append("")

    if scorecard["notes"]:
        out.append("## Notes")
        out.append("")
        out.extend(f"- {note}" for note in scorecard["notes"])
        out.append("")

    out.append("---")
    out.append("")
    out.append("Deterministic metrics are computed by code from recorded provider output. "
               "Judge scores are semantic judgements and are reported separately; they are "
               "never merged into a single ranking number. `n/a` means the metric could not "
               "be computed for that arm, which is not the same as zero.")
    out.append("")
    return "\n".join(out)


def write_markdown(scorecard: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(scorecard), encoding="utf-8", newline="\n")
    return p


# --------------------------------------------------------------------------------------
# Ablation appendix
# --------------------------------------------------------------------------------------


def render_ablation(ablation: dict[str, dict], *, layer: str = "search") -> str:
    """Render the ablation table: recall lost when each provider is removed."""
    out = [f"# Ablation ({layer})", ""]
    out.append("Recall of the ensemble with one provider removed. A large drop means the "
               "provider is load-bearing; zero means the ensemble covers its work.")
    out.append("")
    rows = [
        [name, data.get("ensemble_recall"), data.get("recall_without_provider"),
         data.get("recall_drop")]
        for name, data in sorted(ablation.items())
    ]
    out.extend(_table(["provider", "ensemble recall", "recall without it", "recall drop"], rows))
    out.append("")
    return "\n".join(out)


def write_ablation(ablation: dict[str, dict], path: str | Path, *, layer: str = "search") -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_ablation(ablation, layer=layer), encoding="utf-8", newline="\n")
    return p


def judge_block(verdicts: Sequence[dict], *, provider: str, model: str, blind: bool,
                calibration: CalibrationReport | None = None) -> dict[str, Any]:
    data = summaries(verdicts)
    data.update({"provider": provider, "model": model, "blind": blind})
    if calibration is not None:
        data["calibration"] = calibration.to_dict()
        data["grade"] = calibration_grade(calibration)
    return data


__all__ = [
    "SCORECARD_VERSION", "SCHEMA_VERSION",
    "SearchArm", "FetchArm", "E2EArm",
    "build_scorecard", "write_scorecard", "render_markdown", "write_markdown",
    "render_ablation", "write_ablation", "judge_block",
]
