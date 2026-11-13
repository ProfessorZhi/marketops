"""Command line entry point: ``python -m benchmark.runner.cli <command>``.

Every command is offline. ``score`` reads recorded provider output; it never calls a
provider and never needs a credential. Live runs are driven by an external harness that
writes the same recorded layout, so scoring code is identical in both modes -- the only
difference is who produced the files.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from . import __version__
from .judge import calibrate
from .metrics import (
    GoldLabel,
    SearchResult,
    ablate_cases,
    domain_diversity,
    duplicate_rate,
    e2e_scores,
    fetch_case_scores,
    market_language_accuracy,
    ndcg_at_k,
    normalize_url,
    official_source_rate,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)
from .models import Dataset, DatasetError, RecordedRun, load_dataset
from .normalize import NormalizeError, e2e_delivered, fetch_result, search_rows
from .report import (
    E2EArm,
    FetchArm,
    SearchArm,
    build_scorecard,
    write_ablation,
    write_markdown,
    write_scorecard,
)


def _meta(body: dict) -> dict:
    meta = body.get("_meta")
    return meta if isinstance(meta, dict) else {}


def _latency(body: dict) -> float | None:
    value = _meta(body).get("latency_ms")
    return float(value) if isinstance(value, (int, float)) else None


def _cost(body: dict) -> float:
    value = _meta(body).get("cost_usd")
    return float(value) if isinstance(value, (int, float)) else 0.0


# --------------------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    ds = load_dataset(args.dataset, name=args.name, validate=True)
    print(f"ok: {ds.counts['search']} search, {ds.counts['fetch']} fetch, "
          f"{ds.counts['e2e']} e2e  (checksum {ds.checksum[:16]})")
    for split in ("development", "validation", "holdout"):
        sub = ds.split(split)
        print(f"  {split:12s} {sub.counts}")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    ds = load_dataset(args.dataset, name=args.name, validate=True)
    if args.split:
        ds = ds.split(args.split)
    recorded = RecordedRun.load(args.recorded)

    search_arms = _score_search(ds, recorded)
    fetch_arms = _score_fetch(ds, recorded)
    e2e_arms = _score_e2e(ds, recorded)

    calibration = None
    judge_provider = "none"
    judge_model = ""
    if args.verdicts:
        verdicts = _load_json_list(Path(args.verdicts))
        if args.labels:
            labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
            calibration = calibrate(verdicts, labels)
        judge_provider = args.judge_provider
        judge_model = args.judge_model

    scorecard = build_scorecard(
        run_id=args.run_id,
        dataset_name=ds.name,
        dataset_version=args.dataset_version,
        dataset_checksum=ds.checksum,
        split=args.split or "holdout",
        case_counts=ds.counts,
        label_status=ds.label_status(),
        mode="replay",
        repeats=args.repeats,
        operator=args.operator,
        marketops_version=__version__,
        git_commit=args.git_commit,
        python_version=platform.python_version(),
        providers=_providers_block(recorded),
        search_arms=search_arms,
        fetch_arms=fetch_arms,
        e2e_arms=e2e_arms,
        judge_provider=judge_provider,
        judge_model=judge_model,
        judge_blind=True,
        calibration=calibration,
    )
    out = Path(args.out)
    write_scorecard(scorecard, out)
    write_markdown(scorecard, out.with_suffix(".md"))
    print(f"wrote {out} and {out.with_suffix('.md')}")

    ablation = ablate_cases(ds.search, recorded)
    if ablation:
        write_ablation(ablation, out.with_suffix(".ablation.md"))
        print(f"wrote {out.with_suffix('.ablation.md')}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    scorecard = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
    out = write_markdown(scorecard, args.out)
    print(f"wrote {out}")
    return 0


def cmd_calibrate(args: argparse.Namespace) -> int:
    verdicts = _load_json_list(Path(args.verdicts))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    report = calibrate(verdicts, labels)
    payload = report.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


# --------------------------------------------------------------------------------------
# Scoring helpers
# --------------------------------------------------------------------------------------


def _score_search(ds: Dataset, recorded: RecordedRun) -> list[SearchArm]:
    providers = sorted({p for p in (f.parent.name for f in recorded.root.glob("*/*.json"))})
    arms: list[SearchArm] = []
    per_case_rows: dict[str, dict[str, list[SearchResult]]] = {}

    for case in ds.search:
        gold = GoldLabel.from_case(case)
        rows: dict[str, list[SearchResult]] = {}
        for provider in providers:
            body = recorded.get(provider, case["case_id"])
            if body is None:
                continue
            rows[provider] = [
                SearchResult(url=r["url"], rank=r["rank"], title=r["title"], snippet=r["snippet"])
                for r in search_rows(provider, body)
            ]
        per_case_rows[case["case_id"]] = rows

        for provider, results in rows.items():
            arm = _arm(arms, provider)
            arm.per_case.append(
                {
                    "precision_at_5": precision_at_k(results, gold, 5),
                    "precision_at_10": precision_at_k(results, gold, 10),
                    "recall_at_5": recall_at_k(results, gold, 5),
                    "recall_at_10": recall_at_k(results, gold, 10),
                    "mrr": reciprocal_rank(results, gold),
                    "ndcg_at_10": ndcg_at_k(results, gold, 10),
                    "official_source_rate": official_source_rate(results, gold),
                    "domain_diversity": domain_diversity(results),
                    "duplicate_rate": duplicate_rate(results),
                    "market_language_accuracy": market_language_accuracy(
                        results, case.get("market", ""), case.get("language_code", "")
                    ),
                    "freshness_pass_rate": None,
                }
            )
            body = recorded.get(provider, case["case_id"]) or {}
            latency = _latency(body)
            if latency is not None:
                arm.latencies_ms.append(latency)
            arm.costs_usd.append(_cost(body))
            arm.gold_total += len(gold.relevant)
            arm.relevant_found += sum(
                1 for r in results if r.url and normalize_url(r.url) in gold.relevant
            )
        for provider in providers:
            if provider in rows:
                continue
            body = recorded.get(provider, case["case_id"])
            if body is not None:
                _arm(arms, provider).failures.append(
                    {"case_id": case["case_id"], "error_code": "empty",
                     "message": "recorded body held no usable results"}
                )

    for arm in arms:
        # unique_found was accumulated as a share of each case's gold; recompute exactly.
        arm.unique_found = _unique_total(per_case_rows, ds.search, arm.provider)
    return arms


def _unique_total(rows: dict[str, dict[str, list[SearchResult]]], cases: list[dict],
                  provider: str) -> int:
    total = 0
    for case in cases:
        case_rows = rows.get(case["case_id"]) or {}
        if provider not in case_rows:
            continue
        gold = set(GoldLabel.from_case(case).relevant)
        mine = {normalize_url(r.url) for r in case_rows[provider]} & gold
        others: set[str] = set()
        for other, results in case_rows.items():
            if other != provider:
                others |= {normalize_url(r.url) for r in results} & gold
        total += len(mine - others)
    return total


def _arm(arms: list[SearchArm], provider: str) -> SearchArm:
    for arm in arms:
        if arm.provider == provider:
            return arm
    arm = SearchArm(provider=provider)
    arms.append(arm)
    return arm


def _score_fetch(ds: Dataset, recorded: RecordedRun) -> list[FetchArm]:
    readers = sorted({f.parent.name for f in recorded.root.glob("*/*.json")})
    arms: list[FetchArm] = []
    for reader in readers:
        arm = FetchArm(reader=reader)
        for case in ds.fetch:
            body = recorded.get(reader, case["case_id"])
            if body is None:
                continue
            result = fetch_result(reader, body, case.get("url", ""))
            scores = fetch_case_scores(result, case)
            arm.per_case.extend([scores])
            latency = _latency(body)
            if latency is not None:
                arm.latencies_ms.append(latency)
            arm.costs_usd.append(_cost(body))
            if scores["ok"] and not scores["shell_page"]:
                arm.useful += 1
            if not scores["ok"]:
                arm.failures.append(
                    {"case_id": case["case_id"], "error_code": result.error_code or "failed",
                     "message": f"{reader} failed on {case.get('task_type')}"}
                )
        if arm.per_case:  # a directory that matched no fetch case is not an arm
            arms.append(arm)
    return arms


def _score_e2e(ds: Dataset, recorded: RecordedRun) -> list[E2EArm]:
    configs = sorted({f.parent.name for f in recorded.root.glob("*/*.json")})
    arms: list[E2EArm] = []
    for config in configs:
        arm = E2EArm(configuration=config)
        for case in ds.e2e:
            body = recorded.get(config, case["case_id"])
            if body is None:
                continue
            delivery = e2e_delivered(config, body)
            scores = e2e_scores(_entities(delivery), case)
            arm.per_case.append(scores)
            latency = _latency(body)
            if latency is not None:
                arm.latencies_ms.append(latency)
            arm.costs_usd.append(_cost(body))
            arm.verified_entities += scores["verified_entities"]
            arm.verified_contacts += scores["verified_contacts"]
        if arm.per_case:
            arms.append(arm)
    return arms


def _entities(delivery: dict):
    from .metrics import DeliveredEntity

    return [
        DeliveredEntity(
            entity_id=e.get("entity_id", ""),
            name=e.get("name", ""),
            official_url=e.get("official_url"),
            contacts=tuple(e.get("contacts") or ()),
        )
        for e in delivery["entities"]
    ]


def _providers_block(recorded: RecordedRun) -> dict:
    block = {}
    for provider in recorded.providers():
        manifest = (recorded.manifest.get("providers") or {}).get(provider) or {}
        block[provider] = {
            "version": manifest.get("version", "unknown"),
            "endpoint": manifest.get("endpoint", "recorded"),
            "region_params_sent": manifest.get("region_params_sent", {}),
            "api_quota_used": manifest.get("api_quota_used", 0),
        }
    return block


def _load_json_list(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


# --------------------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.runner.cli",
        description="MarketOps provider benchmark (offline).",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser("validate", help="validate every case file against its schema")
    v.add_argument("--dataset", default="benchmark/datasets/pilot")
    v.add_argument("--name", default="pilot")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("score", help="score recorded provider output")
    s.add_argument("--dataset", default="benchmark/datasets/pilot")
    s.add_argument("--name", default="pilot")
    s.add_argument("--recorded", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--split", default="", choices=["", "development", "validation", "holdout"])
    s.add_argument("--dataset-version", default="v1")
    s.add_argument("--run-id", default="offline")
    s.add_argument("--repeats", type=int, default=1)
    s.add_argument("--operator", default="offline")
    s.add_argument("--git-commit", default="unknown")
    s.add_argument("--verdicts", default="")
    s.add_argument("--labels", default="")
    s.add_argument("--judge-provider", default="none")
    s.add_argument("--judge-model", default="")
    s.set_defaults(func=cmd_score)

    r = sub.add_parser("report", help="render a scorecard as markdown")
    r.add_argument("--scorecard", required=True)
    r.add_argument("--out", required=True)
    r.set_defaults(func=cmd_report)

    c = sub.add_parser("calibrate", help="compare judge verdicts with human labels")
    c.add_argument("--verdicts", required=True)
    c.add_argument("--labels", required=True)
    c.set_defaults(func=cmd_calibrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (DatasetError, NormalizeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
