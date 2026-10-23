#!/usr/bin/env python3
"""Project already-filtered business Sheets into a client view (CSV + XLSX).

This module is deliberately *not* a second admission or grading engine.  The
plugin's formal delivery decides what may leave the candidate store; this helper
only projects the Sheets that survived that filter.  It therefore **refuses**
(and writes nothing) when the caller hands it a row that still carries a HOLD,
an unresolved conflict, or no source citation.  Silently dropping such rows
would turn a caller bug into a client-facing table whose row count nobody can
explain.

Output is always ``CANDIDATE_NOT_APPROVED``.  Nothing here authorises sending,
a formal SOP, or a business-verified contact route.

Reuses ``_xlsx.Sheet`` / ``_xlsx.render_workbook`` unchanged and writes every
cell that starts like a formula as literal text (``DANGEROUS_PREFIXES``).
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from _xlsx import Sheet, render_workbook

__all__ = [
    "ClientViewError",
    "VERSION",
    "STATUS",
    "write_client_view",
    "verify_client_view",
]

VERSION = "0.1.0-client-view"
SCHEMA = "marketops.client-view/1"
STATUS = "CANDIDATE_NOT_APPROVED"
SEND_AUTHORIZED = False
FORMAL_SOP_CLAIMED = False
BUSINESS_VERIFICATION = "NOT_PERFORMED"

XLSX_NAME = "Client_View.xlsx"
MANIFEST_NAME = "MANIFEST.json"
NOTES_SHEET = "客户视图说明"
PROVENANCE_SHEET = "来源索引"
RESERVED_SHEET_NAMES = frozenset({NOTES_SHEET, PROVENANCE_SHEET})

#: Leading characters a spreadsheet would read as a formula.
DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

#: A HOLD marker must be a token, not a substring: "Holding Ltd" is a name.
_HOLD_TOKEN = re.compile(r"^\s*HOLD(?:[_:\-\s]|$)", re.IGNORECASE)
_CONFLICT_TOKEN = re.compile(r"conflict|冲突", re.IGNORECASE)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
#: Exact status claims that this helper may not certify.  The substring
#: "APPROVED" inside ``CANDIDATE_NOT_APPROVED`` must not trigger this.
_APPROVAL_CLAIMS = frozenset({
    "APPROVED", "FORMALLY_APPROVED", "SOP_APPROVED", "READY", "VERIFIED", "FINAL",
})
_UNKNOWN_CITATIONS = frozenset({"", "NONE", "NULL", "UNKNOWN", "N/A", "NA", "-", "—", "/"})

_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_MAX_CELL_CHARS = 32_000
_ROW_LIMIT = 20_000
_MAX_SHEETS = 200


class ClientViewError(ValueError):
    """A refusal with a stable reason code (the message starts with it)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass(frozen=True)
class _Admitted:
    sheets: tuple[Sheet, ...]
    provenance_columns: dict[str, tuple[str, ...]]
    referential_integrity: str
    evidence_id_count: int | None
    checked_rows: int


def _fail(code: str, detail: str = "") -> None:
    raise ClientViewError(code, detail)


def _is_unknown(value: object) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text.upper() in _UNKNOWN_CITATIONS or text in _UNKNOWN_CITATIONS


def _is_xml_char(code: int) -> bool:
    if 0xD800 <= code <= 0xDFFF:
        return False
    return (code in (0x09, 0x0A, 0x0D) or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD or 0x10000 <= code <= 0x10FFFF)


def _safe_text(text: str, *, sheet: str, row: int, column: str) -> str:
    """Reject what XLSX/CSV cannot round-trip identically; normalise newlines."""
    if len(text) > _MAX_CELL_CHARS:
        _fail("CELL_TOO_LONG", f"{sheet}!{row}:{column} has {len(text)} chars")
    for char in text:
        if not _is_xml_char(ord(char)):
            _fail("UNSAFE_CELL_CHARACTER",
                  f"{sheet}!{row}:{column} contains U+{ord(char):04X}")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _literal(value: str) -> str:
    """Never let untrusted text be interpreted as a spreadsheet formula."""
    return "'" + value if value.lstrip().startswith(DANGEROUS_PREFIXES) else value


def _provenance_headers(headers: Sequence[str]) -> tuple[str, ...]:
    found = []
    for header in headers:
        name = header.strip()
        if "资料引用" in name or name in ("资料ID", "来源URL"):
            found.append(header)
    return tuple(found)


def _split_refs(value: object) -> tuple[str, ...]:
    text = "" if value is None else str(value).strip()
    if text.upper() in _UNKNOWN_CITATIONS:
        return ()
    return tuple(part.strip() for part in re.split(r"[;,]", text) if part.strip())


def _admit(sheets: Sequence[Sheet]) -> _Admitted:
    """Verify the input is *already* the filtered business projection.

    Refusal is the whole point: the alternative (drop silently) would make the
    client-visible row count depend on a filter the caller did not run.
    """
    if isinstance(sheets, (str, bytes)) or not isinstance(sheets, Sequence):
        _fail("SHEETS_NOT_A_SEQUENCE", "pass a sequence of _xlsx.Sheet")
    if not sheets:
        _fail("NO_INPUT_SHEETS")
    if len(sheets) > _MAX_SHEETS:
        _fail("TOO_MANY_SHEETS", str(len(sheets)))

    seen_names: set[str] = set()
    provenance: dict[str, tuple[str, ...]] = {}
    checked_rows = 0
    evidence_ids: set[str] | None = None
    reference_columns: set[str] = set()

    for sheet in sheets:
        if not isinstance(sheet, Sheet):
            _fail("INPUT_NOT_A_SHEET", type(sheet).__name__)
        name = sheet.name.strip()
        lowered = name.casefold()
        if lowered in seen_names:
            _fail("DUPLICATE_INPUT_SHEET", name)
        seen_names.add(lowered)
        if name in RESERVED_SHEET_NAMES:
            _fail("RESERVED_SHEET_NAME", name)
        headers = tuple(sheet.headers)
        if len(set(headers)) != len(headers):
            _fail("DUPLICATE_HEADER", name)
        for header in headers:
            _safe_text(str(header), sheet=name, row=1, column=str(header))
        if len(sheet.rows) > _ROW_LIMIT:
            _fail("TOO_MANY_ROWS", f"{name} has {len(sheet.rows)} rows")
        prov = _provenance_headers(headers)
        if not prov:
            _fail("SHEET_HAS_NO_PROVENANCE_COLUMN", name)
        provenance[name] = prov

        id_header = headers[0].strip() if headers else ""
        id_index = 0 if id_header.upper().endswith("ID") else None
        seen_ids: set[str] = set()

        for offset, row in enumerate(sheet.rows, start=2):
            checked_rows += 1
            values = tuple(row)
            for header, value in zip(headers, values):
                if isinstance(value, str):
                    text = _safe_text(value, sheet=name, row=offset, column=header)
                    if _PRIVATE_KEY.search(text):
                        _fail("PRIVATE_KEY_MATERIAL",
                              f"{name}!{offset}:{header} looks like a private key")
                    if _HOLD_TOKEN.search(text):
                        _fail("HOLD_ROW_REJECTED",
                              f"{name}!{offset}:{header}={text[:80]!r}")
                    if _CONFLICT_TOKEN.search(text):
                        _fail("CONFLICT_MARKER",
                              f"{name}!{offset}:{header}={text[:80]!r}")
                    if "状态" in header and text.strip().upper() in _APPROVAL_CLAIMS:
                        _fail("APPROVAL_CLAIM_REJECTED",
                              f"{name}!{offset}:{header}={text!r}")
                else:
                    _safe_text(str(value), sheet=name, row=offset, column=header)
            # A row without any citation is not a client-visible fact.
            if not any(not _is_unknown(values[headers.index(col)]) for col in prov):
                _fail("PROVENANCE_MISSING", f"{name}!{offset} has no source citation")
            if id_index is not None:
                raw_id = values[id_index]
                if not _is_unknown(raw_id):
                    identity = str(raw_id).strip()
                    if identity in seen_ids:
                        _fail("DUPLICATE_RECORD_KEY", f"{name}!{offset}={identity!r}")
                    seen_ids.add(identity)

        if "资料ID" in headers:
            index = headers.index("资料ID")
            ids = {str(row[index]).strip() for row in sheet.rows
                   if not _is_unknown(row[index])}
            if evidence_ids is None:
                evidence_ids = set()
            overlap = evidence_ids & ids
            if overlap:
                _fail("DUPLICATE_RECORD_KEY",
                      f"{name} repeats evidence id {sorted(overlap)[0]!r}")
            evidence_ids |= ids
        for header in prov:
            if "资料引用" in header:
                reference_columns.add(header)

    if evidence_ids is not None and reference_columns:
        for sheet in sheets:
            headers = tuple(sheet.headers)
            indexes = [i for i, h in enumerate(headers)
                       if "资料引用" in h]
            for offset, row in enumerate(sheet.rows, start=2):
                for index in indexes:
                    for ref in _split_refs(row[index]):
                        if ref not in evidence_ids:
                            _fail("PROVENANCE_UNKNOWN_REF",
                                  f"{sheet.name}!{offset}:{headers[index]}={ref!r}")
        integrity = "CHECKED"
    elif evidence_ids is not None:
        integrity = "CHECKED_NO_REFERENCE_COLUMNS"
    else:
        integrity = "NOT_CHECKED_NO_EVIDENCE_SHEET"

    return _Admitted(
        sheets=tuple(sheets),
        provenance_columns=provenance,
        referential_integrity=integrity,
        evidence_id_count=None if evidence_ids is None else len(evidence_ids),
        checked_rows=checked_rows,
    )


def _output_value(value: object) -> str:
    """Every client-view cell is text: IDs keep leading zeros and nothing coerces."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


def _output_sheets(admitted: _Admitted, *, label: str, generated_at: str) -> list[Sheet]:
    data: list[Sheet] = []
    provenance_rows: list[tuple[str, ...]] = []
    total_rows = 0

    for sheet in admitted.sheets:
        headers = tuple(_literal(_safe_text(str(h), sheet=sheet.name, row=1, column=str(h)))
                        for h in sheet.headers)
        rows = []
        prov_indexes = [sheet.headers.index(col) for col in admitted.provenance_columns[sheet.name]]
        for row_number, row in enumerate(sheet.rows, start=2):
            values = tuple(_output_value(v) for v in row)
            values = tuple(_literal(v) for v in values)
            rows.append(values)
            refs: list[str] = []
            for index in prov_indexes:
                text = str(row[index]) if row[index] is not None else ""
                if not _is_unknown(text) and text not in refs:
                    refs.append(_literal(text))
            record_id = _literal(str(row[0])) if row and row[0] is not None else ""
            provenance_rows.append((sheet.name, str(row_number), record_id, "; ".join(refs)))
        total_rows += len(rows)
        data.append(Sheet(sheet.name, headers, tuple(rows),
                          widths=tuple(min(45.0, max(15.0, len(h) * 2.5)) for h in headers)))

    notes = Sheet(NOTES_SHEET, ("字段", "值"), tuple(
        (_literal(field), _literal(value)) for field, value in (
            ("视图标签", label),
            ("生成时间", generated_at),
            ("数据状态", STATUS),
            ("发送授权", "FALSE"),
            ("正式 SOP", "FALSE_NOT_CLAIMED"),
            ("业务核验", BUSINESS_VERIFICATION),
            ("输入来源", "FILTERED_BUSINESS_SHEETS_ONLY"),
            ("契约版本", f"{SCHEMA}+CLIENT_VIEW_CONTRACT"),
            ("数据表数", str(len(data))),
            ("数据行数", str(total_rows)),
            ("说明", "只投影已过滤业务 Sheet；不含 HOLD/无引文/冲突项；"
                     "本视图不构成发送授权或正式 SOP。"),
        )
    ), widths=(18.0, 70.0))
    index_sheet = Sheet(PROVENANCE_SHEET, ("业务表", "行号", "记录ID", "资料引用"),
                        tuple(provenance_rows), widths=(18.0, 8.0, 26.0, 48.0))
    return [notes, index_sheet, *data]


def _normalise_xlsx(data: bytes) -> bytes:
    """Fixed ZIP timestamps: the same inputs must produce the same bytes."""
    source = zipfile.ZipFile(io.BytesIO(data))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            payload = source.read(info.filename)
            fixed = zipfile.ZipInfo(info.filename, date_time=(2020, 1, 1, 0, 0, 0))
            fixed.compress_type = zipfile.ZIP_DEFLATED
            fixed.external_attr = 0o644 << 16
            target.writestr(fixed, payload)
    return output.getvalue()


def _readback_csv(path: Path, sheet: Sheet) -> None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    expected = [list(sheet.headers)] + [list(row) for row in sheet.rows]
    if rows != expected:
        _fail("CSV_READBACK_MISMATCH", sheet.name)


def _cell_text(cell: ET.Element) -> str:
    if cell.get("t") == "inlineStr":
        node = cell.find(".//x:t", _NS)
        return "" if node is None else (node.text or "")
    node = cell.find("x:v", _NS)
    return "" if node is None else (node.text or "")


def _readback_xlsx(path: Path, sheets: Sequence[Sheet]) -> None:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            _fail("XLSX_ARCHIVE_CORRUPT", str(path))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [node.get("name") for node in workbook.findall(".//x:sheet", _NS)]
        if names != [sheet.name for sheet in sheets]:
            _fail("XLSX_SHEET_NAME_MISMATCH", repr(names))
        for index, sheet in enumerate(sheets, start=1):
            root = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            rows = root.findall(".//x:sheetData/x:row", _NS)
            expected = [sheet.headers, *sheet.rows]
            if len(rows) != len(expected):
                _fail("XLSX_ROW_COUNT_MISMATCH", sheet.name)
            for row, expected_values in zip(rows, expected):
                cells = row.findall("x:c", _NS)
                if len(cells) != len(expected_values):
                    _fail("XLSX_COLUMN_COUNT_MISMATCH", sheet.name)
                for cell, value in zip(cells, expected_values):
                    if _cell_text(cell) != ("" if value is None else str(value)):
                        _fail("XLSX_CELL_MISMATCH", sheet.name)


def _file_meta(path: Path) -> dict:
    payload = path.read_bytes()
    return {"bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def _write_staging(staging: Path, sheets: Sequence[Sheet]) -> list[str]:
    xlsx = staging / XLSX_NAME
    xlsx.write_bytes(_normalise_xlsx(render_workbook(list(sheets))))
    _readback_xlsx(xlsx, sheets)
    files = [XLSX_NAME]
    for sheet in sheets:
        path = staging / f"{sheet.name}.csv"
        with path.open("x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(sheet.headers)
            writer.writerows(sheet.rows)
        _readback_csv(path, sheet)
        files.append(path.name)
    return files


def _same_tree(left: Path, right: Path) -> bool:
    left_files = {p.name for p in left.iterdir() if p.is_file()}
    right_files = {p.name for p in right.iterdir() if p.is_file()}
    if left_files != right_files or any(p.is_dir() for p in right.iterdir()):
        return False
    return all((left / name).read_bytes() == (right / name).read_bytes()
               for name in left_files)


def _validate_label(label: str) -> str:
    if not isinstance(label, str) or not label.strip():
        _fail("LABEL_REQUIRED", "label must be a non-empty string")
    text = label.strip()
    if len(text) > 200:
        _fail("LABEL_TOO_LONG", str(len(text)))
    if any(not _is_xml_char(ord(char)) for char in text):
        _fail("LABEL_HAS_CONTROL_CHARACTER", repr(text))
    return text


def _validate_generated_at(value: object) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    text = str(value).strip()
    if not text:
        _fail("GENERATED_AT_EMPTY")
    return _safe_text(text, sheet=MANIFEST_NAME, row=0, column="generated_at")


def write_client_view(destination: Path, sheets: Sequence[Sheet], *,
                      label: str, generated_at: object = None) -> dict:
    """Write ``destination`` (client-view/) as CSV + XLSX + MANIFEST.json.

    ``sheets`` must already be the plugin's filtered *business* Sheets.  A HOLD
    row, a conflict marker, a duplicate business key, a citation that does not
    exist in the supplied evidence sheet, or a row without any citation makes
    this function raise before the destination is created.  It never filters.

    Returns a receipt-shaped metadata dict containing ``files`` (bytes/sha256),
    ``provenance``, objective candidate status and ``manifest_sha256``.  The
    same metadata is persisted as ``MANIFEST.json`` so the integrator can merge
    ``files`` / ``counts`` into the final delivery MANIFEST.
    """
    admitted = _admit(sheets)
    clean_label = _validate_label(label)
    stamp = _validate_generated_at(generated_at)

    raw = Path(destination).expanduser()
    if raw.is_symlink():
        _fail("DESTINATION_IS_SYMLINK", str(raw))
    destination = raw.resolve()
    if destination.exists() and not destination.is_dir():
        _fail("DESTINATION_NOT_A_DIRECTORY", str(destination))

    output_sheets = _output_sheets(admitted, label=clean_label, generated_at=stamp)
    counts = {sheet.name: len(sheet.rows) for sheet in admitted.sheets}
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".client-view-", dir=parent))
    try:
        names = _write_staging(staging, output_sheets)
        files = {name: _file_meta(staging / name) for name in sorted(names)}
        manifest = {
            "schema": SCHEMA,
            "generator": "research_client_view.py",
            "generator_version": VERSION,
            "artifact": "client-view",
            "label": clean_label,
            "generated_at": stamp,
            "status": STATUS,
            "send_authorized": SEND_AUTHORIZED,
            "formal_sop": FORMAL_SOP_CLAIMED,
            "formal_sop_claimed": FORMAL_SOP_CLAIMED,
            "business_verification": BUSINESS_VERIFICATION,
            "input_source": "FILTERED_BUSINESS_SHEETS_ONLY",
            "format": "client-view/xlsx+csv+manifest",
            "output_sheets": [sheet.name for sheet in output_sheets],
            "data_sheets": [sheet.name for sheet in admitted.sheets],
            "counts": counts,
            "row_count": sum(counts.values()),
            "provenance": {
                "contract": "CLIENT_VIEW_CONTRACT.md",
                "provenance_columns": {
                    name: list(columns)
                    for name, columns in admitted.provenance_columns.items()
                },
                "referential_integrity": admitted.referential_integrity,
                "evidence_ids": admitted.evidence_id_count,
                "checked_rows": admitted.checked_rows,
            },
            "files": files,
            "manifest": MANIFEST_NAME,
        }
        manifest_path = staging / MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        verify_client_view(staging)

        if destination.exists():
            if destination.is_dir() and not any(destination.iterdir()):
                # A caller may have pre-created the empty client-view directory.
                os.rename(staging, destination)
            elif _same_tree(staging, destination):
                shutil.rmtree(staging, ignore_errors=True)
                existing = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
                return {**existing, "directory": str(destination),
                        "xlsx_path": str(destination / XLSX_NAME),
                        "manifest_path": str(destination / MANIFEST_NAME),
                        "manifest_sha256": _file_meta(destination / MANIFEST_NAME)["sha256"],
                        "verified": True, "replayed": True}
            else:
                _fail("DESTINATION_CONFLICT",
                      f"{destination} exists with different content; refusing to overwrite")
        else:
            os.rename(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    manifest_sha = _file_meta(destination / MANIFEST_NAME)["sha256"]
    return {**manifest, "directory": str(destination),
            "xlsx_path": str(destination / XLSX_NAME),
            "manifest_path": str(destination / MANIFEST_NAME),
            "manifest_sha256": manifest_sha, "verified": True, "replayed": False}


def verify_client_view(destination: Path) -> dict:
    """Re-read a written client view from disk; never writes anything."""
    root = Path(destination).expanduser().resolve()
    if not root.is_dir():
        _fail("CLIENT_VIEW_NOT_FOUND", str(root))
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        _fail("MANIFEST_MISSING", str(manifest_path))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != STATUS:
        _fail("MANIFEST_STATUS_INVALID", repr(manifest.get("status")))
    if manifest.get("send_authorized") is not False:
        _fail("MANIFEST_SEND_AUTHORIZATION_INVALID")
    if manifest.get("formal_sop_claimed") is not False:
        _fail("MANIFEST_FORMAL_SOP_INVALID")

    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        _fail("MANIFEST_FILES_INVALID")
    actual = {p.name for p in root.iterdir() if p.is_file()}
    if actual != set(files) | {MANIFEST_NAME}:
        _fail("MANIFEST_FILE_SET_MISMATCH",
              f"expected {sorted(set(files) | {MANIFEST_NAME})}, got {sorted(actual)}")
    for name, meta in files.items():
        path = root / name
        payload = path.read_bytes()
        if len(payload) != meta.get("bytes"):
            _fail("MANIFEST_SIZE_MISMATCH", name)
        if hashlib.sha256(payload).hexdigest() != meta.get("sha256"):
            _fail("MANIFEST_HASH_MISMATCH", name)

    counts = manifest.get("counts") or {}
    for name, expected_rows in counts.items():
        path = root / f"{name}.csv"
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        if len(rows) != int(expected_rows) + 1:
            _fail("CSV_ROW_COUNT_MISMATCH", name)

    with zipfile.ZipFile(root / XLSX_NAME) as archive:
        if archive.testzip() is not None:
            _fail("XLSX_ARCHIVE_CORRUPT", XLSX_NAME)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [node.get("name") for node in workbook.findall(".//x:sheet", _NS)]
    if names != list(manifest.get("output_sheets") or []):
        _fail("XLSX_SHEET_NAME_MISMATCH", repr(names))

    return {
        "status": "VERIFIED_CANDIDATE_NOT_APPROVED",
        "schema": manifest.get("schema"),
        "label": manifest.get("label"),
        "files_verified": len(files) + 1,
        "counts": counts,
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Verify an existing client view; generation stays a library call."""
    import argparse
    parser = argparse.ArgumentParser(description="Verify a generated client view")
    parser.add_argument("destination", type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_client_view(args.destination),
                         ensure_ascii=False, indent=2))
        return 0
    except (ClientViewError, OSError, ValueError) as exc:
        import sys
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
