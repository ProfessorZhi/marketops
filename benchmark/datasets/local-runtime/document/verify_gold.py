#!/usr/bin/env python3
"""Check that every gold marker in cases.jsonl is actually in its document.

This runs before any parser is benchmarked. A marker that the source document
does not contain would make a correct parser look wrong, so the manifest has to
be verified against the documents themselves, not against parser output.

Text documents must contain every marker. Scanned documents must contain none
of them in a text layer -- that is what makes OCR necessary, and if a text
layer appeared the case would be silently measuring the wrong thing.

Run from the document/ directory:
    /data/home/fjd/marketops-lab/envs/docbench/bin/python verify_gold.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import docx
import openpyxl
import pymupdf
from pptx import Presentation

HERE = pathlib.Path(__file__).parent


def read_text(case: dict) -> str:
    p = HERE / case["path"]
    kind = case["doc_type"]
    if kind.startswith("pdf"):
        return "".join(page.get_text() for page in pymupdf.open(p))
    if kind == "docx":
        d = docx.Document(str(p))
        parts = [x.text for x in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.extend(cell.text for cell in row.cells)
        return "\n".join(parts)
    if kind == "pptx":
        prs = Presentation(str(p))
        return "\n".join(
            sh.text_frame.text
            for s in prs.slides
            for sh in s.shapes
            if sh.has_text_frame
        )
    if kind == "xlsx":
        wb = openpyxl.load_workbook(p)
        parts = list(wb.sheetnames)
        for ws in wb:
            for row in ws.iter_rows(values_only=True):
                parts.extend(str(v) for v in row if v is not None)
        return "\n".join(parts)
    raise SystemExit(f"unknown doc_type {kind}")


def main() -> int:
    cases = [json.loads(line) for line in (HERE / "cases.jsonl").read_text().splitlines() if line]
    failures = 0
    for case in cases:
        text = read_text(case)
        expected = case["expected"]
        markers = list(expected.get("text_markers", []))
        markers += expected.get("expected_emails", [])
        markers += expected.get("expected_phones", [])

        if case["doc_type"] == "pdf_scanned":
            ok = len(text.strip()) == 0
            detail = "text layer empty (OCR required)" if ok else f"{len(text)} chars leaked into text layer"
        else:
            missing = [m for m in markers if m not in text]
            short = len(text) < expected["min_text_chars"]
            ok = not missing and not short
            detail = f"{len(text)} chars" + (f", missing {missing}" if missing else "")
            if short:
                detail += f", below min_text_chars={expected['min_text_chars']}"

        failures += 0 if ok else 1
        print(f"{'ok  ' if ok else 'FAIL'} {case['case_id']:24} {detail}")

    print(f"\n{len(cases) - failures}/{len(cases)} cases verified")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
