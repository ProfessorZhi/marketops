"""One command surface for the Research Core benchmark.

    validate   schema-check every case in every stage
    list       the adapters and what each stage can run
    run        one stage against one provider, writing run/manifest/summary
    suite      all nine stages against the reference adapters, in order
    contract   the mock provider matrix (7 providers x 10 failure modes)
    report     render a Markdown report from the latest suite run

Nothing here needs a network or an API key: every provider is a mock. A live
provider would be registered in the same registry and would appear in the same
manifest fields, which is the point of the adapter contract.

    python -m benchmark.research_core.cli suite --repeat 3
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from . import runner
from .adapters import mock as mock_adapters
from .adapters.base import AdapterRegistry
from .mocks import contract as contract_mod
from .model import load_dataset_dir

REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_DATASET_ROOT = REPO / "benchmark" / "datasets" / "research-core" / "synthetic"
PUBLIC_DATASET_ROOT = REPO / "benchmark" / "datasets" / "research-core" / "public-evidence"
DEFAULT_OUT_DIR = REPO / "benchmark" / "research-core" / "runs"

#: The reference adapter per stage. These are baselines, not products: a score
#: here is what an offline rule-based pipeline achieves on this dataset.
REFERENCE = {
    "brief": "mock_brief_rules",
    "search": "mock_serper",
    "fetch": "mock_local_http",
    "extraction": "mock_extractor_regex",
    "qualification": "mock_qualification_rules",
    "identity": "mock_identity_rules",
    "evidence": "mock_evidence_checker",
    "channel": "mock_channel_rules",
    "e2e": "mock_research_e2e",
}

STAGES = tuple(REFERENCE)


def _registry() -> AdapterRegistry:
    return mock_adapters.registry()


def cmd_validate(args: argparse.Namespace) -> int:
    roots = [args.dataset_root, *args.also]
    stages = [args.stage] if args.stage else list(STAGES)
    total = 0
    failures = 0
    for root in roots:
        print(f"# {root}")
        root_total = 0
        for stage in stages:
            if not (root / stage).is_dir():
                # A dataset holds the stages it has. Absent is skipped, not failed;
                # an empty *present* directory is still a failure.
                print(f"     {stage:<14} -- (no {stage}/ in this dataset)")
                continue
            try:
                cases = load_dataset_dir(root, stage)
            except Exception as exc:  # noqa: BLE001 - reported, not raised
                print(f"FAIL {stage:<14} {type(exc).__name__}: {exc}")
                failures += 1
                continue
            ids = {case["case_id"] for case in cases}
            root_total += len(cases)
            print(f"ok   {stage:<14} {len(cases):>3} cases, {len(ids)} unique case_id")
        total += root_total
        if not root_total:
            print(f"FAIL {root}: no cases validated")
            failures += 1
    print(f"\n{total} cases across {len(roots)} dataset(s); {failures} stage/dataset(s) failed")
    return 1 if failures else 0


def cmd_list(args: argparse.Namespace) -> int:
    registry = _registry()
    for row in registry.describe():
        print(f"{row['stage']:<14} {row['name']:<28} kind={row['kind']:<6} "
              f"usable={row['usable']} requires={','.join(row['requires']) or '-'}")
        if row["notes"]:
            print(f"{'':<14} {row['notes']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    registry = _registry()
    provider = args.provider or REFERENCE[args.stage]
    adapter = registry.get(provider, stage=args.stage)
    result = runner.execute(
        dataset_root=args.dataset_root, stage=args.stage, adapter=adapter,
        repeat=args.repeat, out_dir=args.out_dir, config={"repeat": args.repeat},
        notes=args.notes,
    )
    manifest = result["manifest"]
    print(f"run_id={manifest['run_id']} provider={manifest['provider']} "
          f"cases={manifest['counts']['cases']} scored={manifest['counts']['scored']}")
    print(f"manifest={result['manifest_path']}")
    print(f"results={result['results_path']}")
    if manifest.get("metrics"):
        print(json.dumps(manifest["metrics"], indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_suite(args: argparse.Namespace) -> int:
    registry = _registry()
    summaries: dict[str, dict] = {}
    failed = False
    for stage in (args.stages or STAGES):
        if not (args.dataset_root / stage).is_dir():
            # Suite runs the stages this dataset holds; the summary records only those.
            print(f"skip {stage:<14} (no {stage}/ under {args.dataset_root})")
            continue
        provider = REFERENCE[stage]
        adapter = registry.get(provider, stage=stage)
        result = runner.execute(
            dataset_root=args.dataset_root, stage=stage, adapter=adapter,
            repeat=args.repeat, out_dir=args.out_dir,
            config={"repeat": args.repeat, "suite": "reference"},
            notes=f"reference suite: {stage}",
        )
        manifest = result["manifest"]
        summaries[stage] = {
            "provider": manifest["provider"],
            "run_id": manifest["run_id"],
            "counts": manifest["counts"],
            "metrics": manifest["metrics"],
            "manifest_path": str(result["manifest_path"]),
            "results_path": str(result["results_path"]),
            "dataset_checksum": manifest["datasets"][0]["checksum"],
            "dataset_version": manifest["datasets"][0]["version"],
        }
        statuses = {k: v for k, v in manifest["counts"].items() if k not in ("cases", "scored") and v}
        print(f"{stage:<14} {provider:<28} cases={manifest['counts']['cases']:>3} "
              f"scored={manifest['counts']['scored']:>3} {statuses}")
        if manifest["counts"]["failed"] or manifest["counts"]["invalid_output"]:
            failed = True

    path = args.out_dir / f"suite-summary{args.tag}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summaries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nwrote {path}")
    return 1 if failed else 0


def cmd_contract(args: argparse.Namespace) -> int:
    return contract_mod.main()


def cmd_report(args: argparse.Namespace) -> int:
    from .report import render, render_public

    summary_path = args.out_dir / f"suite-summary{args.tag}.json"
    if not summary_path.is_file():
        print(f"no suite summary at {summary_path}; run `suite` first", file=sys.stderr)
        return 2
    summaries = json.loads(summary_path.read_text(encoding="utf-8"))
    text = render(summaries)
    if args.with_public:
        public_path = args.out_dir / "suite-summary-public.json"
        if not public_path.is_file():
            print(f"no public suite summary at {public_path}; run "
                  "`research-public-benchmark` first", file=sys.stderr)
            return 2
        public = json.loads(public_path.read_text(encoding="utf-8"))
        manifest_path = (PUBLIC_DATASET_ROOT / "capture-manifest.json")
        manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                    if manifest_path.is_file() else None)
        text = text.rstrip() + "\n\n" + render_public(public, manifest)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="research-core", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset-root", type=pathlib.Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="schema-check the datasets")
    p_validate.add_argument("--stage", choices=STAGES)
    p_validate.add_argument("--also", action="append", type=pathlib.Path, default=[],
                            metavar="ROOT",
                            help="validate another dataset root too (repeatable)")
    p_validate.set_defaults(func=cmd_validate)

    p_list = sub.add_parser("list", help="list adapters")
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="run one stage")
    p_run.add_argument("--stage", choices=STAGES, required=True)
    p_run.add_argument("--provider", help="adapter name (default: the reference adapter)")
    p_run.add_argument("--repeat", type=int, default=1)
    p_run.add_argument("--notes", default="")
    p_run.set_defaults(func=cmd_run)

    p_suite = sub.add_parser("suite", help="run every stage against the reference adapters")
    p_suite.add_argument("--repeat", type=int, default=1)
    p_suite.add_argument("--stages", nargs="+", choices=STAGES,
                         help="restrict the suite (default: every stage the dataset holds)")
    p_suite.add_argument("--tag", default="", help="suffix for suite-summary<tag>.json")
    p_suite.set_defaults(func=cmd_suite)

    p_contract = sub.add_parser("contract", help="mock provider contract matrix")
    p_contract.set_defaults(func=cmd_contract)

    p_report = sub.add_parser("report", help="render the Markdown report")
    p_report.add_argument("--out", type=pathlib.Path)
    p_report.add_argument("--tag", default="", help="read suite-summary<tag>.json")
    p_report.add_argument("--with-public", action="store_true",
                          help="append the Public Evidence section")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
