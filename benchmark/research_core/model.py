"""Case loading, schema validation and identity for the Research Core harness.

Three identities are recorded for every run, because a number without them cannot
be compared to anything later:

  * ``case_sha256``      -- the canonical bytes of one case (content addressed);
  * ``dataset_checksum`` -- file names + bytes of every file in the dataset;
  * ``dataset_version``  -- the semantic version the case declares.

The envelope schema is shared by every stage; ``input`` and ``gold`` are
validated by stage branches inside the same schema file. That keeps one
validator, one error format and one place to add a stage.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator

from benchmark.runner.models import dataset_checksum, iter_jsonl

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
CASE_SCHEMA_PATH = SCHEMA_DIR / "research-case.schema.json"
RESULT_SCHEMA_PATH = SCHEMA_DIR / "result.schema.json"
MANIFEST_SCHEMA_PATH = SCHEMA_DIR / "run-manifest.schema.json"

STAGES: tuple[str, ...] = (
    "brief", "search", "fetch", "extraction", "qualification",
    "identity", "evidence", "channel", "e2e",
)

#: The six outcomes a row can have. ``empty`` is a real answer, not a failure:
#: a no-results query that correctly returns nothing must not be scored as an
#: outage, and an outage must not be scored as a correct empty result.
STATUSES: tuple[str, ...] = (
    "ok", "empty", "failed", "not_configured", "timeout", "invalid_output", "skipped",
)

#: Only these may enter an aggregate metric. ``empty`` is scored (it can be the
#: right answer); the rest are recorded and excluded, never counted as zero.
SCOREABLE: frozenset[str] = frozenset({"ok", "empty"})


class CaseError(ValueError):
    """Raised when a case file or a case violates the shared contract."""


_cache: dict[str, Any] = {}


def _schema(path: Path) -> dict:
    key = str(path)
    if key not in _cache:
        _cache[key] = json.loads(path.read_text(encoding="utf-8"))
    return _cache[key]


def _validate_against(instance: object, path: Path, what: str) -> None:
    schema = _schema(path)
    try:
        import jsonschema  # type: ignore
    except ImportError as exc:  # pragma: no cover - the venv ships jsonschema
        raise CaseError(
            "jsonschema is required to validate research-core artifacts; "
            "install it instead of silently skipping validation"
        ) from exc
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "<root>"
        raise CaseError(f"{what}: {where}: {first.message}")


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


def iter_cases(path: str | Path) -> Iterator[dict]:
    p = Path(path)
    if not p.exists():
        raise CaseError(f"no such case file: {p}")
    yield from iter_jsonl(p)


def validate_case(case: dict) -> None:
    _validate_against(case, CASE_SCHEMA_PATH, str(case.get("case_id", "?")))


def canonical_bytes(case: dict) -> bytes:
    """Stable bytes for hashing: sorted keys, no whitespace, UTF-8."""
    return json.dumps(case, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def case_sha256(case: dict) -> str:
    return hashlib.sha256(canonical_bytes(case)).hexdigest()


def load_cases(path: str | Path, validate: bool = True) -> list[dict]:
    cases = list(iter_cases(path))
    if validate:
        seen: set[str] = set()
        for case in cases:
            validate_case(case)
            cid = case["case_id"]
            if cid in seen:
                raise CaseError(f"{Path(path).name}: duplicate case_id {cid}")
            seen.add(cid)
    return cases


def load_dataset_dir(root: str | Path, stage: str, validate: bool = True) -> list[dict]:
    """Load ``<root>/<stage>/*.jsonl``.

    A missing directory is an error, not an empty dataset: an empty dataset
    measures nothing and must never look like a pass.
    """
    base = Path(root) / stage
    files = sorted(base.glob("*.jsonl")) if base.is_dir() else []
    if not files:
        raise CaseError(f"{base}: no case files")
    cases: list[dict] = []
    for path in files:
        cases.extend(load_cases(path, validate=validate))
    for case in cases:
        if case["stage"] != stage:
            raise CaseError(f"{case['case_id']}: stage={case['stage']} in a {stage}/ dataset")
    return cases


def dataset_files(root: str | Path, stage: str) -> list[Path]:
    base = Path(root) / stage
    files = sorted(base.glob("*.jsonl"))
    if not files:
        raise CaseError(f"{base}: no case files")
    return files


def dataset_version(cases: list[dict]) -> str:
    versions = {c["dataset_version"] for c in cases}
    if len(versions) != 1:
        raise CaseError(f"one dataset declared several versions: {sorted(versions)}")
    return versions.pop()


def label_status_counts(cases: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        status = case.get("label_status", "unspecified")
        counts[status] = counts.get(status, 0) + 1
    return dict(sorted(counts.items()))


def origin_counts(cases: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for case in cases:
        origin = case.get("origin", "unspecified")
        counts[origin] = counts.get(origin, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# Results and manifests
# ---------------------------------------------------------------------------


def validate_result(row: dict) -> None:
    _validate_against(row, RESULT_SCHEMA_PATH, str(row.get("case_id", "?")))


def validate_manifest(manifest: dict) -> None:
    _validate_against(manifest, MANIFEST_SCHEMA_PATH, str(manifest.get("run_id", "?")))


def dataset_identity(root: str | Path, stage: str, cases: list[dict]) -> dict:
    files = dataset_files(root, stage)
    return {
        "name": f"research-core/{stage}",
        "version": dataset_version(cases),
        "path": str(Path(root) / stage),
        "checksum": dataset_checksum(files),
        "cases": len(cases),
        "label_status": label_status_counts(cases),
    }


def config_sha256(params: dict) -> str:
    return hashlib.sha256(
        json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
