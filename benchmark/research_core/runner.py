"""Execute cases against one adapter and record everything.

The runner writes three artifacts per invocation:

  * ``<run_id>.jsonl``        -- one row per case per repeat, schema-validated;
  * ``<run_id>.manifest.json``-- dataset checksums, config hash, counts, metrics;
  * ``<run_id>.summary.md``   -- the human-readable table.

Design rules this file enforces:

  * a credential-less provider yields ``not_configured`` rows, never ``ok`` rows
    with empty output (which would score as precise);
  * a provider error yields ``failed``, never ``empty``;
  * ``repeat > 1`` re-runs the same cases and *checks* that the gold metrics are
    byte-identical. A mismatch is reported in the manifest, not averaged away.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import secrets
import socket
import time
from pathlib import Path
from typing import Any

from . import metrics as metrics_mod
from .adapters.base import (
    AdapterError,
    AdapterTimeout,
    ContractViolation,
    NotConfigured,
    StageAdapter,
)
from .contracts import violations
from .model import (
    SCOREABLE,
    STATUSES,
    case_sha256,
    config_sha256,
    dataset_identity,
    load_cases,
    validate_manifest,
    validate_result,
)

TOOL_VERSION = "research-core-1.0.0"

#: The one collection whose emptiness defines "the provider answered with nothing".
_PRIMARY = {
    "brief": None, "search": "results", "fetch": "text", "extraction": None,
    "qualification": None, "identity": "clusters", "evidence": None,
    "channel": "ranked_channels", "e2e": "prospects",
}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def new_run_id() -> str:
    stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"rc-{stamp}-{secrets.token_hex(3)}"


def _is_empty(stage: str, output: dict) -> bool:
    key = _PRIMARY.get(stage)
    if key is None:
        return False
    value = output.get(key)
    if key == "text":
        return not (value or "").strip()
    return isinstance(value, (list, dict)) and not value


#: Most specific first: several of these are ``AdapterError`` subclasses, so the
#: order *is* the taxonomy. A new error class must be added above ``AdapterError``.
_ERROR_STATUS: tuple[tuple[type[BaseException], str], ...] = (
    (NotConfigured, "not_configured"),
    (AdapterTimeout, "timeout"),
    (ContractViolation, "invalid_output"),
    (AdapterError, "failed"),
)


def status_for_error(exc: BaseException) -> str:
    """The status a raised exception must produce. Never ``ok`` and never ``empty``."""
    for cls, status in _ERROR_STATUS:
        if isinstance(exc, cls):
            return status
    return "failed"


def probe(adapter: StageAdapter, stage: str, case: dict) -> tuple[str, BaseException | None]:
    """Run one case and return ``(status, error)`` using the runner's own taxonomy.

    The contract test uses this so a mock's failure mode is asserted against the
    same classification a live run would get, not a second copy of the rules.
    """
    try:
        output = adapter.run(case)
        problems = violations(stage, output)
        if problems:
            raise ContractViolation("; ".join(problems[:5]))
        return ("empty" if _is_empty(stage, output) else "ok"), None
    except BaseException as exc:  # noqa: BLE001 - the status is the result
        return status_for_error(exc), exc


def _provenance(dataset_version: str, checksum: str, case: dict) -> dict:
    return {
        "dataset_version": dataset_version,
        "dataset_checksum": checksum,
        "case_sha256": case_sha256(case),
        "host": socket.gethostname(),
        "python": platform.python_version(),
    }


def _row(
    *, run_id: str, stage: str, provider: str, model: str | None, config: dict,
    case: dict, identity: dict, started_at: str, latency: float | None,
    status: str, output: dict | None, error: dict | None, cost: float,
) -> dict:
    row: dict[str, Any] = {
        "run_id": run_id,
        "case_id": case["case_id"],
        "stage": stage,
        "provider": provider,
        "model": model,
        "configuration": config,
        "started_at": started_at,
        "latency_s": round(latency, 3) if latency is not None else None,
        "status": status,
        "output": output,
        "gold": case["gold"],
        "evaluation": {"scored": False, "metrics": {}, "detail": ""},
        "error": error,
        "cost_usd": round(cost, 6),
        "provenance": _provenance(identity["version"], identity["checksum"], case),
    }
    if status in SCOREABLE and output is not None:
        row["evaluation"] = {
            "scored": True,
            "metrics": metrics_mod.evaluate(stage, case, output),
            "detail": (
                "provider ran and returned nothing; scored because empty can be correct"
                if status == "empty" else ""
            ),
        }
    else:
        row["evaluation"]["detail"] = f"not scored: status={status}"
    return row


def execute(
    *,
    dataset_root: Path,
    stage: str,
    adapter: StageAdapter,
    case_file: Path | None = None,
    repeat: int = 1,
    out_dir: Path | None = None,
    config: dict | None = None,
    notes: str = "",
) -> dict:
    config = config or {}
    cases = load_cases(case_file) if case_file else load_cases_list(dataset_root, stage)
    identity = dataset_identity(dataset_root, stage, cases)
    run_id = new_run_id()
    out_dir = out_dir or (Path(__file__).resolve().parents[2] / "benchmark" / "research-core" / "runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"{run_id}-{adapter.info.name}.jsonl"

    started_at = _now_iso()
    started_monotonic = time.monotonic()
    per_repeat_metrics: list[dict] = []
    counts = {status: 0 for status in STATUSES}
    total_rows = 0

    usable, reason = adapter.availability()

    with results_path.open("w", encoding="utf-8") as fh:
        for repetition in range(repeat):
            evaluations: list[dict] = []
            for case in cases:
                case_started = _now_iso()
                clock = time.perf_counter()
                if not usable:
                    row = _row(
                        run_id=run_id, stage=stage, provider=adapter.info.name,
                        model=adapter.info.model, config=config, case=case,
                        identity=identity, started_at=case_started, latency=None,
                        status="not_configured",
                        output=None,
                        error={"kind": "credential", "message": reason, "http_status": None},
                        cost=0.0,
                    )
                else:
                    try:
                        output = adapter.run(case)
                        problems = violations(stage, output)
                        if problems:
                            raise ContractViolation("; ".join(problems[:5]))
                        elapsed = time.perf_counter() - clock
                        status = "empty" if _is_empty(stage, output) else "ok"
                        cost = float(output.pop("_cost_usd", 0.0)) if isinstance(output, dict) else 0.0
                        row = _row(
                            run_id=run_id, stage=stage, provider=adapter.info.name,
                            model=adapter.info.model, config=config, case=case,
                            identity=identity, started_at=case_started, latency=elapsed,
                            status=status, output=output, error=None, cost=cost,
                        )
                    except AdapterError as exc:
                        status = status_for_error(exc)
                        row = _row(
                            run_id=run_id, stage=stage, provider=adapter.info.name,
                            model=adapter.info.model, config=config, case=case,
                            identity=identity, started_at=case_started,
                            # A missing credential never reached the provider, so
                            # there is no latency to report.
                            latency=None if status == "not_configured" else time.perf_counter() - clock,
                            status=status, output=None, error=exc.as_error(), cost=0.0,
                        )
                    except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
                        row = _row(
                            run_id=run_id, stage=stage, provider=adapter.info.name,
                            model=adapter.info.model, config=config, case=case,
                            identity=identity, started_at=case_started,
                            latency=time.perf_counter() - clock, status="failed",
                            output=None,
                            error={"kind": "internal", "message": f"{type(exc).__name__}: {exc}",
                                   "http_status": None},
                            cost=0.0,
                        )

                validate_result(row)
                counts[row["status"]] += 1
                total_rows += 1
                if row["evaluation"]["scored"]:
                    evaluations.append(row["evaluation"]["metrics"])
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

            per_repeat_metrics.append(metrics_mod.aggregate(stage, evaluations))

    finished_at = _now_iso()
    repeat_identical = len({json.dumps(m, sort_keys=True) for m in per_repeat_metrics}) == 1
    metrics_out = per_repeat_metrics[0] if per_repeat_metrics else None
    if repeat > 1:
        metrics_out = dict(metrics_out or {})
        metrics_out["repeat_identical"] = repeat_identical

    manifest = {
        "schema_version": "marketops.research.run-manifest.v1",
        "tool_version": TOOL_VERSION,
        "run_id": run_id,
        "stage": stage,
        "provider": adapter.info.name,
        "model": adapter.info.model,
        "configuration": {"config_sha256": config_sha256(config), "params": config},
        "started_at": started_at,
        "finished_at": finished_at,
        "environment": {
            "host": socket.gethostname(),
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "datasets": [identity],
        "results_path": str(results_path),
        "counts": {
            "cases": len(cases),
            **counts,
            "scored": 0,  # replaced below from the JSONL, which survives repeats
        },
        "metrics": metrics_out,
        "repeat": repeat,
        "notes": notes or f"elapsed={round(time.monotonic() - started_monotonic, 2)}s rows={total_rows}",
    }
    # ``scored`` is computed from the JSONL, not from the status counters above,
    # because a repeat multiplies rows while `cases` stays the case count.
    manifest["counts"]["scored"] = _count_scored(results_path)
    validate_manifest(manifest)
    manifest_path = out_dir / f"{run_id}-{adapter.info.name}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {"manifest": manifest, "manifest_path": manifest_path, "results_path": results_path}


def _count_scored(results_path: Path) -> int:
    total = 0
    with results_path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("evaluation", {}).get("scored"):
                total += 1
    return total


def load_cases_list(dataset_root: Path, stage: str) -> list[dict]:
    from .model import load_dataset_dir

    return load_dataset_dir(dataset_root, stage)


def summarize(manifest: dict) -> str:
    lines = [
        f"# {manifest['stage']} / {manifest['provider']}",
        "",
        f"- run_id: `{manifest['run_id']}`",
        f"- dataset: `{manifest['datasets'][0]['name']}` v{manifest['datasets'][0]['version']}",
        f"- dataset_checksum: `{manifest['datasets'][0]['checksum']}`",
        f"- config_sha256: `{manifest['configuration']['config_sha256']}`",
        f"- repeat: {manifest['repeat']}"
        + (" (gold metrics identical)" if manifest["metrics"] and manifest["metrics"].get("repeat_identical", True) else " (MISMATCH)"),
        "",
        "| status | count |",
        "|---|---|",
    ]
    for status in STATUSES:
        lines.append(f"| {status} | {manifest['counts'][status]} |")
    lines += ["", "| metric | value |", "|---|---|"]
    for key, value in sorted((manifest.get("metrics") or {}).items()):
        if isinstance(value, dict):
            for sub, subvalue in value.items():
                lines.append(f"| {key}.{sub} | {subvalue} |")
        else:
            lines.append(f"| {key} | {value} |")
    return "\n".join(lines) + "\n"


def write_summary(manifest: dict, path: Path) -> Path:
    path.write_text(summarize(manifest), encoding="utf-8")
    return path


def env_flag(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()
