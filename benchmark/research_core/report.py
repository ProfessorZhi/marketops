"""Render the reference-suite summary as a Markdown report.

The report is generated from the manifests, never typed by hand, so every
number in it can be traced to a ``run_id`` and a dataset checksum on disk.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

STAGE_ORDER = ("brief", "search", "fetch", "extraction", "qualification",
               "identity", "evidence", "channel", "e2e")

STAGE_TITLES = {
    "brief": "Product Brief Parsing",
    "search": "Search",
    "fetch": "Fetch",
    "extraction": "Entity / Contact Extraction",
    "qualification": "Qualification",
    "identity": "Identity & Deduplication",
    "evidence": "Evidence & Provenance",
    "channel": "Channel Evaluation",
    "e2e": "Research E2E",
}

#: Metrics below 1.0 in the reference run, with the reason. Maintaining this
#: table by hand is deliberate: a baseline that silently drifts toward 1.0 is
#: indistinguishable from a metric that stopped measuring.
KNOWN_BASELINE_GAPS = {
    "search": (
        "`precision_at_5`/`precision_at_10` are low by construction: the lexical "
        "ranker returns up to ten results while most queries have one or two "
        "relevant companies. Recall, domain hit rate and MRR are the meaningful "
        "figures here."
    ),
    "identity": (
        "`relation_misses=1` is deliberate. RC-IDNT-BRANCH-004 expects a "
        "`branch_of` relation between two same-name companies on different "
        "domains; the baseline will not assert a corporate relationship without a "
        "page that states it, so it reports nothing rather than fabricating one."
    ),
    "channel": (
        "`order_match` is 2/6. The baseline sorts by the prior the brief states; "
        "the gold ranking is editorial and, in two cases, orders channels the "
        "brief rates equally. `top1_correct` and `ndcg` are the order-sensitive "
        "figures that do not depend on that judgement."
    ),
}


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _metrics_table(metrics: dict) -> str:
    rows = []
    for key in sorted(metrics):
        value = metrics[key]
        if key == "scored_cases":
            continue
        if isinstance(value, dict):
            for sub, subvalue in value.items():
                rows.append((f"{key}.{sub}", _fmt(subvalue)))
        else:
            rows.append((key, _fmt(value)))
    lines = ["| metric | value |", "|---|---|"]
    lines += [f"| `{name}` | {value} |" for name, value in rows]
    return "\n".join(lines)


def _status_table(counts: dict) -> str:
    keys = ("cases", "ok", "empty", "failed", "not_configured", "timeout",
            "invalid_output", "skipped", "scored")
    lines = ["| " + " | ".join(keys) + " |", "|" + "---|" * len(keys)]
    lines.append("| " + " | ".join(str(counts.get(k, 0)) for k in keys) + " |")
    return "\n".join(lines)


def render(summaries: dict[str, dict]) -> str:
    now = _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")
    stages = [stage for stage in STAGE_ORDER if stage in summaries]
    total_cases = sum(summaries[s]["counts"]["cases"] for s in stages)
    total_scored = sum(summaries[s]["counts"]["scored"] for s in stages)

    out: list[str] = [
        "# Research Core Reference Run",
        "",
        f"Generated {now} from `benchmark/research-core/runs/suite-summary.json`.",
        "",
        "Every provider below is a mock. These numbers are an offline baseline, "
        "not a product comparison: no live API was called and no key was used.",
        "",
        "## Summary",
        "",
        f"- stages: {len(stages)}",
        f"- cases: {total_cases}",
        f"- scored: {total_scored}",
        f"- not_configured: {sum(summaries[s]['counts']['not_configured'] for s in stages)}",
        f"- failed: {sum(summaries[s]['counts']['failed'] for s in stages)}",
        f"- timeout: {sum(summaries[s]['counts']['timeout'] for s in stages)}",
        f"- invalid_output: {sum(summaries[s]['counts']['invalid_output'] for s in stages)}",
        "",
        "## Datasets",
        "",
        "| stage | version | cases | dataset_checksum |",
        "|---|---|---|---|",
    ]
    for stage in stages:
        entry = summaries[stage]
        out.append(
            f"| {stage} | {entry['dataset_version']} | {entry['counts']['cases']} | "
            f"`{entry['dataset_checksum']}` |"
        )

    out += ["", "## Per-stage results", ""]
    for stage in stages:
        entry = summaries[stage]
        out += [
            f"### {STAGE_TITLES.get(stage, stage)}",
            "",
            f"- provider: `{entry['provider']}`",
            f"- run_id: `{entry['run_id']}`",
            f"- manifest: `{entry['manifest_path']}`",
            "",
            _status_table(entry["counts"]),
            "",
        ]
        if entry.get("metrics"):
            out += [_metrics_table(entry["metrics"]), ""]
        if stage in KNOWN_BASELINE_GAPS:
            out += [f"**Known baseline gap.** {KNOWN_BASELINE_GAPS[stage]}", ""]

    out += [
        "## How to reproduce",
        "",
        "```bash",
        "python -m benchmark.research_core.cli validate",
        "python -m benchmark.research_core.cli contract",
        "python -m benchmark.research_core.cli suite --repeat 3",
        "python -m benchmark.research_core.cli report --out RESEARCH_CORE_TEST_REPORT.md",
        "```",
        "",
    ]
    return "\n".join(out)


def render_public(summaries: dict[str, dict], manifest: dict | None = None) -> str:
    """The Public Evidence section, rendered from a ``suite-summary-public.json``.

    Public evidence is a different benchmark, not an extension of the synthetic
    one: its cases are frozen real pages, so its numbers are separated here and
    never averaged into the reference run above.
    """
    stages = [stage for stage in STAGE_ORDER if stage in summaries]
    out = ["## Public Evidence Benchmark", ""]
    if manifest:
        counts = manifest.get("counts", {})
        out += [
            f"Dataset `{manifest.get('dataset_version')}`. Real public pages, "
            "captured once with `robots.txt` honoured, desensitised on write, and "
            "served from `127.0.0.1:18091`. Scoring reads only the committed bytes.",
            "",
            f"- snapshots frozen: {counts.get('snapshots')}",
            f"- targets recorded as gaps: {counts.get('gaps')} (see "
            "`benchmark/datasets/research-core/public-evidence/GAPS.md`)",
            f"- companies: {counts.get('companies')}; creators: {counts.get('creators')}; "
            f"countries: {counts.get('countries')}; languages: {counts.get('languages')}",
            "",
        ]
    out += [
        "| stage | provider | version | cases | scored | failed | not_configured |",
        "|---|---|---|---|---|---|---|",
    ]
    for stage in stages:
        entry = summaries[stage]
        counts = entry["counts"]
        out.append(
            f"| {stage} | `{entry['provider']}` | {entry['dataset_version']} | "
            f"{counts['cases']} | {counts['scored']} | {counts['failed']} | "
            f"{counts['not_configured']} |"
        )
    out.append("")
    for stage in stages:
        entry = summaries[stage]
        out += [
            f"### {STAGE_TITLES.get(stage, stage)} (public)",
            "",
            f"- run_id: `{entry['run_id']}`",
            f"- dataset_checksum: `{entry['dataset_checksum']}`",
            "",
            _metrics_table(entry.get("metrics") or {}),
            "",
        ]
    return "\n".join(out)
