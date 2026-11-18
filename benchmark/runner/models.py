"""Case models, dataset loading and schema validation.

Datasets are JSONL: one case per line, UTF-8 without BOM. JSONL was chosen over a
single JSON array because benchmark cases get appended and hand-edited over months,
and a syntax error in line 800 must not take the other 799 down with it.

Validation is optional at load time and mandatory in CI (see tests/test_benchmark_*).
``jsonschema`` is used when installed; otherwise a small structural checker runs, so
the offline suite works on a bare Python 3.10+ with no third-party packages.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"

CASE_SCHEMAS = {
    "search": "search-case.schema.json",
    "fetch": "fetch-case.schema.json",
    "e2e": "e2e-case.schema.json",
}


class DatasetError(ValueError):
    """Raised when a case file is unreadable or a case violates its schema."""


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield cases from a JSONL file, naming the offending line on failure."""
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            try:
                yield json.loads(text)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc


def load_cases(path: str | Path, validate: bool = False) -> list[dict]:
    """Load one JSONL case file (or a JSON array held in a .json file)."""
    p = Path(path)
    if not p.exists():
        raise DatasetError(f"no such case file: {p}")
    if p.suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise DatasetError(f"{p.name}: a .json dataset must hold a list of cases")
        cases = data
    else:
        cases = list(iter_jsonl(p))
    if validate:
        layer = _layer_of(p)
        for case in cases:
            validate_case(case, layer)
    return cases


def _layer_of(path: Path) -> str:
    for layer in CASE_SCHEMAS:
        if layer in path.parts or path.stem.startswith(layer):
            return layer
    raise DatasetError(f"cannot tell the layer of {path}; expected search/fetch/e2e in the path")


@dataclass
class Dataset:
    """A named set of cases plus its identity for the scorecard."""

    name: str
    root: Path
    search: list[dict] = field(default_factory=list)
    fetch: list[dict] = field(default_factory=list)
    e2e: list[dict] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {"search": len(self.search), "fetch": len(self.fetch), "e2e": len(self.e2e)}

    @property
    def checksum(self) -> str:
        return dataset_checksum(self.files)

    def split(self, split_name: str) -> "Dataset":
        def pick(cases: list[dict]) -> list[dict]:
            return [c for c in cases if c.get("split") == split_name]

        return Dataset(
            name=f"{self.name}:{split_name}",
            root=self.root,
            search=pick(self.search),
            fetch=pick(self.fetch),
            e2e=pick(self.e2e),
            files=self.files,
        )

    def label_status(self) -> dict[str, int]:
        """How many cases carry each ``label_status``, across all three layers.

        A scorecard must be able to say whether its gold labels are ``draft`` or
        ``human_verified``. Without this the artefact that gets quoted is
        indistinguishable from one computed on confirmed labels, and the whole
        point of keeping the pilot at ``draft`` is lost at the last step.
        """
        counts: dict[str, int] = {}
        for layer in ("search", "fetch", "e2e"):
            for case in getattr(self, layer):
                status = case.get("label_status", "unspecified")
                counts[status] = counts.get(status, 0) + 1
        return dict(sorted(counts.items()))

    def by_id(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for layer in ("search", "fetch", "e2e"):
            for case in getattr(self, layer):
                out[case["case_id"]] = case
        return out

    def duplicate_ids(self) -> list[str]:
        seen: set[str] = set()
        dupes: list[str] = []
        for layer in ("search", "fetch", "e2e"):
            for case in getattr(self, layer):
                cid = case.get("case_id")
                if cid in seen:
                    dupes.append(cid)
                seen.add(cid)
        return dupes


def load_dataset(root: str | Path, name: str = "pilot", validate: bool = False) -> Dataset:
    """Load ``<root>/search/*.jsonl``, ``<root>/fetch/*.jsonl``, ``<root>/e2e/*.jsonl``."""
    base = Path(root)
    if not base.is_dir():
        raise DatasetError(f"no such dataset directory: {base}")
    ds = Dataset(name=name, root=base)
    for layer in ("search", "fetch", "e2e"):
        for path in sorted((base / layer).glob("*.jsonl")):
            ds.files.append(path)
            getattr(ds, layer).extend(load_cases(path, validate=validate))
    if not ds.files:
        # A typo'd path used to yield "ok: 0 search, 0 fetch, 0 e2e" and exit 0.
        # An empty dataset measures nothing and must not look like a pass.
        raise DatasetError(
            f"{base}: no case files (expected search/, fetch/, e2e/ each holding *.jsonl)"
        )
    if validate:
        dupes = ds.duplicate_ids()
        if dupes:
            raise DatasetError(f"duplicate case ids: {sorted(set(dupes))}")
    return ds


def dataset_checksum(files: Iterable[Path]) -> str:
    """sha256 over file names and bytes, in sorted order.

    Name is included so renaming a file changes the checksum even when its content
    does not; a benchmark result must state exactly which artefact produced it.
    """
    digest = hashlib.sha256()
    for path in sorted(Path(f) for f in files):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------

_SCHEMA_CACHE: dict[str, Any] = {}


def _schema(layer: str) -> dict:
    if layer not in _SCHEMA_CACHE:
        path = SCHEMA_DIR / CASE_SCHEMAS[layer]
        _SCHEMA_CACHE[layer] = json.loads(path.read_text(encoding="utf-8"))
    return _SCHEMA_CACHE[layer]


def validate_case(case: dict, layer: str) -> None:
    """Validate against the JSON Schema, falling back to structural checks."""
    if layer not in CASE_SCHEMAS:
        raise DatasetError(f"unknown layer {layer!r}")
    schema = _schema(layer)
    try:
        import jsonschema  # type: ignore
    except ImportError:
        _structural_check(case, schema)
        return
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(case), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.path) or "<root>"
        raise DatasetError(f"{case.get('case_id', '?')}: {where}: {first.message}")


def _structural_check(case: dict, schema: dict) -> None:
    """The subset of JSON Schema this project actually relies on.

    Only the keywords used by the three case schemas are implemented: required,
    const, enum, type, pattern, minItems, and properties/additionalProperties. That is
    enough to keep the offline suite honest without vendoring a validator.
    """
    cid = case.get("case_id", "?")
    for key in schema.get("required", []):
        if key not in case:
            raise DatasetError(f"{cid}: missing required field {key!r}")
    props = schema.get("properties", {})
    if schema.get("additionalProperties") is False:
        extra = set(case) - set(props)
        if extra:
            raise DatasetError(f"{cid}: unexpected fields {sorted(extra)}")
    for key, spec in props.items():
        if key not in case:
            continue
        value = case[key]
        _check_value(cid, key, value, spec)


def _check_value(cid: str, key: str, value: Any, spec: dict) -> None:
    if "const" in spec and value != spec["const"]:
        raise DatasetError(f"{cid}: {key} must be {spec['const']!r}")
    if "enum" in spec and value not in spec["enum"]:
        raise DatasetError(f"{cid}: {key}={value!r} is not one of {spec['enum']}")
    types = spec.get("type")
    if types:
        allowed = types if isinstance(types, list) else [types]
        if not any(_is_type(value, t) for t in allowed):
            raise DatasetError(f"{cid}: {key} has the wrong type (expected {allowed})")
    if "pattern" in spec and isinstance(value, str):
        import re

        if not re.search(spec["pattern"], value):
            raise DatasetError(f"{cid}: {key}={value!r} does not match {spec['pattern']}")
    if "minItems" in spec and isinstance(value, list) and len(value) < spec["minItems"]:
        raise DatasetError(f"{cid}: {key} needs at least {spec['minItems']} items")


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    if name == "null":
        return value is None
    return True


# --------------------------------------------------------------------------------------
# Recorded provider output (replay mode)
# --------------------------------------------------------------------------------------


@dataclass
class RecordedRun:
    """Raw provider output captured during a live run, replayed offline.

    Layout: ``<root>/<provider>/<case_id>.json`` holding the provider's own response
    body verbatim, plus ``<root>/manifest.json`` with run metadata. Verbatim storage is
    the point: a replayed benchmark that stores a parsed summary can never catch a
    parser regression.
    """

    root: Path
    manifest: dict = field(default_factory=dict)

    @classmethod
    def load(cls, root: str | Path) -> "RecordedRun":
        base = Path(root)
        manifest_path = base / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return cls(root=base, manifest=manifest)

    def get(self, provider: str, case_id: str) -> dict | None:
        path = self.root / provider / f"{case_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def providers(self) -> list[str]:
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def case_ids(self, provider: str) -> list[str]:
        return sorted(p.stem for p in (self.root / provider).glob("*.json"))
