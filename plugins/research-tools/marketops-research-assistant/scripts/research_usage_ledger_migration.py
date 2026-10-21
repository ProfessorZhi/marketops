#!/usr/bin/env python3
"""Bring the two legacy attempt ledgers into the unified one, without touching them.

The legacy stores are ``state/dual-search-ledger.sqlite3`` (``search_attempts``) and
``state/provider-reservations.sqlite3`` (``provider_reservations``). Both counted
attempts with one global ``COUNT(*)`` and neither knew about money.

## The rules this module keeps

**No legacy database is ever opened. Not even to read it.** Every source file is
hashed, copied to scratch, the copy's hash is compared with the original's, and the
*rows* are read out of the copy. Afterwards the originals are hashed again; a changed
hash aborts the run and says so.

This is stricter than "open it read-only", and the difference is not academic: opening
a WAL-mode SQLite file with ``mode=ro`` still creates ``-wal`` and ``-shm`` next to it,
and on a directory that is not writable it fails outright. A plan that leaves two new
files beside a customer database is a plan that modified the thing it promised not to
touch, whatever the bytes of the main file say. Copying first is also why this works
on a read-only filesystem.

**A row that cannot be mapped is reported, not dropped.** Legacy rows whose
identifiers do not match the ledger's shape, and stores whose table is missing or
whose columns have drifted, appear in the report with a reason. The reconciliation
block states the gap instead of closing it quietly.

**Imported rows do not consume today's ceilings.** They arrive as ``UNKNOWN`` -
because a legacy reserved attempt may or may not have been charged, and there is no
evidence either way - but with zero money and ``counts_against_caps = 0``. That is
a units decision, not a convenience: legacy ceilings counted *attempts globally*,
and the new ceilings are per-scope USD and quota. Folding historical attempts into
a freshly configured USD ceiling would invent a ceiling nobody set. The exposure is
preserved, visible and reconcilable either way, and
``--legacy-rows-count-against-caps`` opts into the other reading explicitly.

**Dry run is the default.** ``migrate`` writes only with ``--apply``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Iterator

try:  # inside the plugin package
    from . import research_usage_ledger as usage
except ImportError:  # standalone import from scripts/
    import research_usage_ledger as usage

__all__ = ["LegacyStore", "LEGACY_STORES", "plan", "migrate", "MigrationRefused", "main"]

SCHEMA = "marketops.usage.migration.v1"


class MigrationRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LegacyStore:
    """One legacy table, described once so the report and the reader cannot disagree."""

    name: str
    relative_path: str
    table: str
    columns: tuple[str, ...]
    run_column: str = "run_id"
    operation_column: str = "operation_id"
    outcome_column: str = ""
    provider_column: str = ""
    timestamp_column: str = ""

    @property
    def identity(self) -> str:
        return f"{self.relative_path}:{self.table}"


#: The legacy shapes as they were actually written. ``provider_reservations`` grew
#: two columns by migration, so the reader tolerates a table without them.
LEGACY_STORES = (
    LegacyStore(
        name="dual-search", relative_path="state/dual-search-ledger.sqlite3",
        table="search_attempts",
        columns=("run_id", "operation_id", "fingerprint", "provider", "reserved_at", "state"),
        provider_column="provider", timestamp_column="reserved_at",
    ),
    LegacyStore(
        name="provider-reservations", relative_path="state/provider-reservations.sqlite3",
        table="provider_reservations",
        columns=("run_id", "operation_id", "action", "signature", "created_at", "host_key",
                 "outcome_code"),
        outcome_column="outcome_code", timestamp_column="created_at",
    ),
)


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_copy(path: Path) -> sqlite3.Connection:
    """Open a **copy** for reading. Never called with a legacy path.

    ``mode=ro`` plus ``query_only`` is what protects the copy; the protection for the
    original is that it is never handed to SQLite at all.
    """
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=1")
    return db


def _verified_copy(source: Path, scratch: Path, name: str) -> Path:
    """A byte-identical copy of a legacy file, verified before anything reads it."""
    copy = scratch / f"{name}.sqlite3"
    shutil.copy2(source, copy)
    if _sha256_file(copy) != _sha256_file(source):
        raise MigrationRefused(
            "COPY_MISMATCH",
            f"{name} changed between the hash and the copy; nothing was read from it")
    return copy


def _table_columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def _iter_rows(db: sqlite3.Connection, store: LegacyStore, columns: Iterable[str]
               ) -> Iterator[sqlite3.Row]:
    ordered = ", ".join(columns)
    yield from db.execute(f"SELECT {ordered} FROM {store.table} ORDER BY rowid")


def _content_hash(columns: Iterable[str], rows: Iterable[sqlite3.Row]) -> str:
    """A hash over the columns and rows **actually read**.

    Over the readable intersection, not over the declared set: a store that drifted
    by dropping a column would otherwise fail to hash at all, which would turn a
    reportable drift into a crash.
    """
    readable = list(columns)
    digest = sha256()
    for row in rows:
        digest.update(("|".join(str(row[column]) for column in readable) + "\n")
                      .encode("utf-8"))
    return digest.hexdigest()


def _store_plan(store: LegacyStore, source: Path, scratch: Path) -> dict:
    """Everything read about one legacy store, read out of a verified copy."""
    entry: dict[str, Any] = {
        "name": store.name, "path": str(source), "table": store.table,
        "exists": source.is_file(), "bytes": 0, "sha256": "", "rows": 0,
        "content_sha256": "", "missing_columns": [], "unmappable": [], "by_run": {},
    }
    if not entry["exists"]:
        return entry
    try:
        # Hashing reads the file too, so it belongs inside the guard: a store the
        # process may not read is a reported condition, not a traceback - the operator
        # needs to see which store and why.
        entry["bytes"] = source.stat().st_size
        entry["sha256"] = _sha256_file(source)
        copy = _verified_copy(source, scratch, store.name)
    except (MigrationRefused, OSError) as exc:
        entry["unreadable"] = getattr(exc, "code", type(exc).__name__)
        return entry
    try:
        with _read_copy(copy) as db:
            entry["table_columns"] = sorted(_table_columns(db, store.table))
            if not entry["table_columns"]:
                entry["unmappable"].append({"reason": "TABLE_MISSING", "table": store.table})
                return entry
            needed = set(store.columns)
            entry["missing_columns"] = sorted(needed - set(entry["table_columns"]))
            readable = [column for column in store.columns if column in entry["table_columns"]]
            rows = list(_iter_rows(db, store, readable))
            entry["rows"] = len(rows)
            entry["content_sha256"] = _content_hash(readable, rows)
            for row in rows:
                run_id = str(row[store.run_column] or "")
                operation_id = str(row[store.operation_column] or "")
                if not usage.ID.fullmatch(run_id) or not usage.ID.fullmatch(operation_id):
                    entry["unmappable"].append({
                        "reason": "IDENTIFIER_NOT_MAPPABLE", "run_id_length": len(run_id),
                        "operation_id_length": len(operation_id)})
                    continue
                entry["by_run"][run_id] = entry["by_run"].get(run_id, 0) + 1
    except sqlite3.Error as exc:
        entry["unmappable"].append({"reason": "STORE_UNREADABLE",
                                    "detail": type(exc).__name__})
    finally:
        copy.unlink(missing_ok=True)
    entry["mappable"] = entry["rows"] - len(entry["unmappable"])
    return entry


def plan(root: str | Path, *, ledger_path: str | Path | None = None) -> dict:
    """Read-only survey: what is there, what would move, and what would not.

    Reads copies, not the originals - see the module docstring - so this is safe on a
    read-only filesystem and leaves no ``-wal`` beside a customer database.
    """
    base = Path(root)
    scratch = Path(tempfile.mkdtemp(prefix="marketops-ledger-survey-"))
    try:
        stores = [_store_plan(store, base / store.relative_path, scratch)
                  for store in LEGACY_STORES]
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    ledger = Path(ledger_path) if ledger_path is not None else None
    return {
        "schema": SCHEMA, "action": "plan", "root": str(base),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stores": stores,
        "ledger_path": str(ledger) if ledger else "",
        "totals": {
            "stores_present": sum(1 for store in stores if store["exists"]),
            "source_rows": sum(store["rows"] for store in stores),
            "mappable_rows": sum(store.get("mappable", 0) for store in stores),
            "unmappable_rows": sum(len(store["unmappable"]) for store in stores),
        },
        "note": (
            "A plan writes nothing. Legacy files are read for their hash and their rows only; "
            "the ledger is not created."),
    }


def migrate(root: str | Path, ledger_path: str | Path, *, dry_run: bool = True,
            project: str = "legacy-import", counts_against_caps: bool = False) -> dict:
    """Copy, verify, read the copy, and import. ``dry_run`` writes nothing.

    Returns the reconciliation report. ``dry_run`` still produces the full plan, so
    an operator can see the row counts before allowing a write.
    """
    if not usage.ID.fullmatch(project):
        raise MigrationRefused("INVALID_PROJECT", "the importing project must be a short id")
    base = Path(root)
    target = Path(ledger_path)
    _refuse_inside_checkout(target)
    survey = plan(base, ledger_path=target)
    if dry_run:
        return {**survey, "action": "dry_run", "written": False,
                "would_import": survey["totals"]["mappable_rows"],
                "counts_against_caps": counts_against_caps, "project": project}
    ledger = usage.UsageLedger(target)
    before = len(ledger.operations())
    scratch = Path(tempfile.mkdtemp(prefix="marketops-ledger-migration-"))
    stores_report: list[dict] = []
    total_imported = total_skipped = 0
    try:
        for store, planned in zip(LEGACY_STORES, survey["stores"]):
            entry = dict(planned)
            entry["imported_rows"] = 0
            entry["skipped_existing_rows"] = 0
            if not entry["exists"]:
                entry["skip_reason"] = "SOURCE_MISSING"
                stores_report.append(entry)
                continue
            if entry.get("unreadable"):
                entry["skip_reason"] = "SOURCE_UNREADABLE"
                stores_report.append(entry)
                continue
            if not entry.get("table_columns"):
                entry["skip_reason"] = "TABLE_MISSING"
                stores_report.append(entry)
                continue
            source = base / store.relative_path
            copy = _verified_copy(source, scratch, store.name)
            entry["copied_sha256"] = _sha256_file(copy)
            # The applied report describes the rows actually read out of the copy,
            # so the plan's reading is replaced rather than appended to.
            entry["unmappable"] = []
            for record, refusal in _map_rows(store, copy, project):
                if refusal is not None:
                    entry["unmappable"].append(refusal)
                    continue
                _, created = ledger.import_legacy_record(
                    **record, counts_against_caps=counts_against_caps)
                if created:
                    entry["imported_rows"] += 1
                    total_imported += 1
                else:
                    entry["skipped_existing_rows"] += 1
                    total_skipped += 1
            entry["mappable"] = entry["rows"] - len(entry["unmappable"])
            entry["source_sha256_after"] = _sha256_file(source)
            entry["source_bytes_unchanged"] = entry["source_sha256_after"] == entry["sha256"]
            if not entry["source_bytes_unchanged"]:
                raise MigrationRefused(
                    "SOURCE_CHANGED_DURING_MIGRATION",
                    f"{store.name} was modified during the migration; stop and investigate")
            entry["source_sidecars"] = sorted(
                path.name for path in source.parent.glob(source.name + "-*"))
            stores_report.append(entry)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    after = ledger.operations()
    legacy_rows = sum(1 for row in after if row.origin == "legacy")
    mappable = survey["totals"]["mappable_rows"]
    reconciliation = {
        "source_rows": survey["totals"]["source_rows"],
        "mappable_source_rows": mappable,
        "unmappable_source_rows": survey["totals"]["unmappable_rows"],
        "ledger_rows_with_origin_legacy": legacy_rows,
        "unaccounted_rows": mappable - legacy_rows,
        "reconciled": mappable - legacy_rows == 0,
    }
    return {
        **survey, "action": "apply", "written": True, "stores": stores_report,
        "project": project, "counts_against_caps": counts_against_caps,
        "ledger_path": str(target), "ledger_rows_before": before,
        "ledger_rows_after": len(after), "imported_rows": total_imported,
        "skipped_existing_rows": total_skipped, "reconciliation": reconciliation,
        "note": (
            "Imported rows are UNKNOWN: a legacy reserved attempt may or may not have been "
            "charged. They carry no money and, unless asked otherwise, do not consume today's "
            "ceilings."),
    }


def _map_rows(store: LegacyStore, copy: Path, project: str
              ) -> Iterator[tuple[dict, dict | None]]:
    """Yield (row-for-the-ledger, unmappable-reason) from a verified copy."""
    with _read_copy(copy) as db:
        available = _table_columns(db, store.table)
        if not available:
            return
        readable = [column for column in store.columns if column in available]
        for row in _iter_rows(db, store, readable):
            run_id = str(row[store.run_column] or "")
            operation_id = str(row[store.operation_column] or "")
            if not usage.ID.fullmatch(run_id) or not usage.ID.fullmatch(operation_id):
                yield {}, {"reason": "IDENTIFIER_NOT_MAPPABLE", "table": store.table}
                continue
            values = {column: row[column] for column in readable}
            yield {
                "legacy_ref": f"{store.relative_path}:{store.table}:{run_id}:{operation_id}",
                "project": project, "run_id": run_id, "provider": _provider(store, values),
                "capability": _capability(store, values),
                "transport": f"legacy_{store.name.replace('-', '_')}",
                "created_at": _created_at(store, values),
                "outcome": str(values.get(store.outcome_column, "") or ""),
            }, None


_PROVIDER_BY_ACTION = {
    "serper": "serper", "serpapi": "serpapi", "tinyfish_search": "tinyfish",
    "tinyfish_fetch": "tinyfish", "crawl4ai_local": "crawl4ai",
    "crawl4ai_cloud": "crawl4ai", "jev_choice": "typesafe_jev",
    "tinyfish_mcp_search": "tinyfish", "tinyfish_mcp_fetch": "tinyfish",
    "tinyfish_mcp_agent": "tinyfish", "tinyfish_mcp_wait": "tinyfish",
    "serpapi_mcp_search": "serpapi",
}


def _provider(store: LegacyStore, values: dict) -> str:
    """A provider name from the legacy column, or from the action it recorded."""
    if store.provider_column and values.get(store.provider_column):
        candidate = str(values[store.provider_column])
        if usage.ID.fullmatch(candidate):
            return candidate
    action = str(values.get("action", "") or "")
    if action in _PROVIDER_BY_ACTION:
        return _PROVIDER_BY_ACTION[action]
    return "legacy"


def _capability(store: LegacyStore, values: dict) -> str:
    action = str(values.get("action", "") or "")
    if action in _PROVIDER_BY_ACTION:
        return action
    return "search" if store.name == "dual-search" else "legacy_attempt"


def _created_at(store: LegacyStore, values: dict) -> str:
    """Keep the legacy timestamp when it is usable, so history keeps its order."""
    raw = str(values.get(store.timestamp_column, "") or "") if store.timestamp_column else ""
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).isoformat()
    if moment.tzinfo is None:
        return datetime.now(timezone.utc).isoformat()
    return moment.astimezone(timezone.utc).isoformat()


def _refuse_inside_checkout(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if any((part / ".git").exists() for part in (resolved.parent, *resolved.parents)):
        raise MigrationRefused("LEDGER_INSIDE_CHECKOUT",
                               "refusing to write a ledger inside a Git checkout")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="research_usage_ledger_migration",
        description="Plan or apply the legacy-ledger migration. Planning writes nothing.")
    parser.add_argument("command", choices=("plan", "migrate"))
    parser.add_argument("--root", required=True, help="the customer data root holding state/")
    parser.add_argument("--ledger", help="the unified ledger database to write")
    parser.add_argument("--project", default="legacy-import")
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it, migrate only reports")
    parser.add_argument("--legacy-rows-count-against-caps", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            payload = plan(args.root, ledger_path=args.ledger)
            print(json.dumps(payload, indent=2, default=str))
            return 0
        if not args.ledger:
            raise MigrationRefused("LEDGER_PATH_REQUIRED",
                                   "migrate needs --ledger to know where to write")
        payload = migrate(args.root, args.ledger, dry_run=not args.apply, project=args.project,
                          counts_against_caps=args.legacy_rows_count_against_caps)
        print(json.dumps(payload, indent=2, default=str))
        if args.apply and not payload["reconciliation"]["reconciled"]:
            return 3
        return 0
    except (MigrationRefused, usage.LedgerRefused) as exc:
        print(json.dumps({"status": "REFUSED", "code": exc.code, "message": exc.message}))
        return 2
    except (sqlite3.Error, OSError) as exc:
        # A store the process cannot read is a reported refusal, not a traceback: an
        # operator on a read-only filesystem needs to know which store and why.
        print(json.dumps({"status": "REFUSED", "code": "STORE_UNREADABLE",
                          "message": f"{type(exc).__name__}: {exc}"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
