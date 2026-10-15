#!/usr/bin/env python3
"""Export validated MarketOps research stages as Excel-safe CSV; NOT an XLSX writer."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    from .research_pack import TABLES, verify
except ImportError:  # direct script invocation: python scripts/export_excel_safe_csv.py
    from research_pack import TABLES, verify

DANGEROUS_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")

def literal_cell(value: str) -> str:
    """Prevent formula execution on Excel import, including after whitespace."""
    text = value or ""
    return "'" + text if text.lstrip().startswith(DANGEROUS_PREFIXES) else text

def export_pack(source: Path, output: Path) -> dict:
    report = verify(source)
    if report["result"] == "INVALID":
        raise ValueError("Source pack has blocking integrity errors: " + "; ".join(report["errors"][:10]))
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Refusing to overwrite an existing nonempty Excel handoff directory")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, headers in TABLES.items():
        path = output / f"{name}.csv"
        with (source / "stages" / f"{name}.csv").open(newline="", encoding="utf-8-sig") as reader_file:
            incoming = csv.DictReader(reader_file)
            with path.open("x", newline="", encoding="utf-8-sig") as writer_file:
                outgoing = csv.DictWriter(writer_file, fieldnames=headers, lineterminator="\r\n")
                outgoing.writeheader()
                for row in incoming:
                    outgoing.writerow({h: literal_cell(row.get(h, "")) for h in headers})
        manifest[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / "SUMMARY.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "README.txt").write_text(
        "MarketOps Excel-safe CSV handoff, not an XLSX workbook and not a verified customer database.\n"
        "All external values with formula-like prefixes have been imported as literal text.\n"
        "Check SUMMARY.json for actual rows and errors. Ready routes never authorize email sending.\n",
        encoding="utf-8",
    )
    # The journal records this verified local handoff automatically; it does not
    # infer that a customer has accepted the research result or authorized outreach.
    try:
        from .research_event_journal import append_event
    except ImportError:
        from research_event_journal import append_event
    brief = json.loads((source / "intake.json").read_text(encoding="utf-8"))
    run_id = brief["request_id"]
    operation_id = "export-" + hashlib.sha256(str(output.resolve()).encode("utf-8")).hexdigest()[:24]
    ledger_root = os.environ.get("MARKETOPS_RESEARCH_DATA_ROOT") or source.expanduser().resolve().parent
    receipt = append_event(ledger_root, run_id=run_id, operation_id=operation_id,
                           stage="export", kind="export", status="succeeded", provider="local")
    return {"result": "CSV_HANDOFF_CREATED_NOT_XLSX", "files": len(TABLES),
            "row_counts": report["counts"], "output": str(output), "journal": receipt}

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pack", type=Path)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    try:
        print(json.dumps(export_pack(args.pack, args.output), indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
