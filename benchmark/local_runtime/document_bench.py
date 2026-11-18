#!/usr/bin/env python3
"""Run the synthetic document set through each parser and score it.

What this measures, and what it deliberately does not:

  * ``text_completeness`` -- the fraction of gold markers present. The markers
    were verified against the documents by document/verify_gold.py before any
    parser ran, so a miss is the parser's.
  * ``reading_order`` -- the markers named in ``expected.reading_order`` must
    appear in that order. Column-major extraction breaks this.
  * ``table_accuracy`` -- the fraction of gold cell strings present. Row and
    column counts are recorded when a parser reports structure, but they are
    not scored: MinerU returns markdown, and counting cells in markdown would
    measure the markdown writer rather than the table model.
  * ``page_coverage`` -- detected page count vs the gold count.

It does not compare parse text against the PDF's own extractable text. For a
scanned page that text is empty, and for a text PDF the comparison would only
tell us the two extractors agree, which is not a quality claim.

Usage:
    python -m benchmark.local_runtime.document_bench --parsers pymupdf mineru_local
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC_DIR = ROOT / "benchmark/datasets/local-runtime/document"
RUNS_DIR = ROOT / "benchmark/local-runtime/runs"

MINERU_HOME = pathlib.Path("/data/home/fjd/marketops-lab/mineru-home")
MINERU_BASE = "http://127.0.0.1:18000"
MINERU_TIER = "standard"


# --------------------------------------------------------------------------
# parsers: each returns dict(text, pages, tables, meta)
# --------------------------------------------------------------------------

def parse_pymupdf(case: dict) -> dict:
    import pymupdf

    path = DOC_DIR / case["path"]
    started = time.monotonic()
    doc = pymupdf.open(path)
    parts = []
    tables = 0
    for page in doc:
        parts.append(page.get_text())
        try:
            tables += len(page.find_tables().tables)
        except Exception:
            pass
    return {
        "text": "".join(parts),
        "pages": doc.page_count,
        "tables_detected": tables,
        "latency_s": round(time.monotonic() - started, 3),
        "meta": {"engine": "pymupdf", "version": getattr(pymupdf, "__version__", "unknown")},
    }


def parse_office(case: dict) -> dict:
    import docx
    import openpyxl
    from pptx import Presentation

    path = DOC_DIR / case["path"]
    kind = case["doc_type"]
    started = time.monotonic()
    if kind == "docx":
        d = docx.Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.extend(cell.text for cell in row.cells)
        text, pages = "\n".join(parts), 1
    elif kind == "pptx":
        prs = Presentation(str(path))
        parts = []
        for slide in prs.slides:
            for shape in slide.shapes:
                if shape.has_text_frame:
                    parts.append(shape.text_frame.text)
        text, pages = "\n".join(parts), len(prs.slides)
    elif kind == "xlsx":
        wb = openpyxl.load_workbook(path)
        parts = list(wb.sheetnames)
        for ws in wb:
            for row in ws.iter_rows(values_only=True):
                parts.extend(str(v) for v in row if v is not None)
        text, pages = "\n".join(parts), len(wb.sheetnames)
    else:
        raise ValueError(f"native parser cannot read {kind}")
    return {
        "text": text,
        "pages": pages,
        "tables_detected": None,
        "latency_s": round(time.monotonic() - started, 3),
        "meta": {"engine": f"native-{kind}"},
    }


def _mineru_token() -> str:
    for line in (MINERU_HOME / "config.yaml").read_text().splitlines():
        if line.strip().startswith("token:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("MinerU token not found")


def _mineru_call(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {_mineru_token()}"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(MINERU_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode(errors="replace")[:400]}


def parse_mineru(case: dict) -> dict:
    path = str((DOC_DIR / case["path"]).resolve())
    started = time.monotonic()
    status, resp = _mineru_call("/api/v1/parses", "POST",
                                {"path": path, "tier": MINERU_TIER, "force": True})
    if status != 200:
        raise RuntimeError(f"submit {status}: {resp}")
    ids = resp.get("wait_parse_ids") or resp.get("created_parse_ids") or []
    if not ids:
        raise RuntimeError(f"no parse id (cache_hit={resp.get('cache_hit')})")

    parse_id = ids[0]
    deadline = started + case.get("timeout_seconds", 180) * 2
    record = {}
    while time.monotonic() < deadline:
        _, record = _mineru_call(f"/api/v1/parses/{parse_id}")
        if record.get("status") in ("done", "failed"):
            break
        time.sleep(0.5)
    if record.get("status") != "done":
        raise RuntimeError(f"parse {record.get('status')}: {record.get('error_code')} {record.get('error_msg')}")

    short = record["short_id"]
    query = urllib.parse.urlencode({"tier": MINERU_TIER})
    status, doc = _mineru_call(f"/api/v1/docs/{short}/content?{query}")
    if status != 200:
        raise RuntimeError(f"content {status}: {doc}")
    text = doc.get("content", "")
    page_nums = [int(n) for n in re.findall(r"<!-- page \d+ of (\d+) -->", text)]
    return {
        "text": text,
        "pages": page_nums[0] if page_nums else record.get("coverage") or 1,
        "tables_detected": text.count("<table"),
        "latency_s": round(time.monotonic() - started, 3),
        "meta": {"engine": "mineru", "tier": MINERU_TIER, "parse_id": parse_id,
                 "via": record.get("via"), "sha256": record.get("sha256")},
    }


PARSERS = {
    "pymupdf": (parse_pymupdf, "pdf"),
    "mineru_local": (parse_mineru, "pdf"),
    "native_office": (parse_office, "office"),
}


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def score(case: dict, result: dict) -> dict:
    text = result["text"]
    expected = case["expected"]
    markers = list(expected.get("text_markers", []))
    markers += expected.get("expected_emails", [])
    markers += expected.get("expected_phones", [])
    hit = [m for m in markers if m in text]

    order = expected.get("reading_order", [])
    positions = [text.find(m) for m in order]
    order_ok = bool(order) and all(p >= 0 for p in positions) and positions == sorted(positions)

    cells = [c for table in expected.get("tables", []) for c in table.get("cell_markers", [])]
    cells_hit = [c for c in cells if c in text]

    long_enough = len(text) >= expected.get("min_text_chars", 0)
    return {
        "case_id": case["case_id"],
        "doc_type": case["doc_type"],
        "difficulty": case["difficulty"],
        "text_chars": len(text),
        "pages_expected": expected.get("page_count"),
        "pages_detected": result["pages"],
        "page_coverage": _ratio(result["pages"], expected.get("page_count")),
        "markers_hit": len(hit),
        "markers_total": len(markers),
        "text_completeness": _ratio(len(hit), len(markers)),
        "long_enough": long_enough,
        "missing_markers": [m for m in markers if m not in text],
        "reading_order_checked": bool(order),
        "reading_order_ok": order_ok if order else None,
        "table_cells_hit": len(cells_hit),
        "table_cells_total": len(cells),
        "table_accuracy": _ratio(len(cells_hit), len(cells)) if cells else None,
        "tables_detected": result["tables_detected"],
        "latency_s": result["latency_s"],
        "meta": result["meta"],
    }


def _ratio(num: int | None, den: int | None) -> float | None:
    if num is None or not den:
        return None
    return round(num / den, 4)


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["parser"], row["doc_type"]), []).append(row)
    out = []
    for (parser, doc_type), items in sorted(groups.items()):
        def mean(key, subset=items):
            vals = [i[key] for i in subset if i.get(key) is not None]
            return round(statistics.mean(vals), 4) if vals else None
        order_items = [i for i in items if i["reading_order_checked"]]
        out.append({
            "parser": parser,
            "doc_type": doc_type,
            "cases": len(items),
            "text_completeness": mean("text_completeness"),
            "page_coverage": mean("page_coverage"),
            "table_accuracy": mean("table_accuracy"),
            "reading_order_accuracy": (
                round(sum(1 for i in order_items if i["reading_order_ok"]) / len(order_items), 4)
                if order_items else None
            ),
            "long_enough_rate": round(sum(1 for i in items if i["long_enough"]) / len(items), 4),
            "latency_p50_s": round(statistics.median([i["latency_s"] for i in items]), 2),
            "latency_max_s": round(max(i["latency_s"] for i in items), 2),
            "failures": sum(1 for i in items if i.get("error")),
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parsers", nargs="+", default=list(PARSERS))
    ap.add_argument("--cases", default=str(DOC_DIR / "cases.jsonl"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cases = [json.loads(line) for line in pathlib.Path(args.cases).read_text().splitlines() if line]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_path = pathlib.Path(args.out) if args.out else RUNS_DIR / f"document-bench-{stamp}.jsonl"

    rows = []
    with out_path.open("w", encoding="utf-8") as fh:
        for parser in args.parsers:
            fn, scope = PARSERS[parser]
            for case in cases:
                is_pdf = case["doc_type"].startswith("pdf")
                if (scope == "pdf") != is_pdf:
                    continue
                row = {"parser": parser, "case_id": case["case_id"],
                       "doc_type": case["doc_type"], "difficulty": case["difficulty"]}
                try:
                    result = fn(case)
                    row.update(score(case, result))
                except Exception as exc:
                    row.update({"error": f"{type(exc).__name__}: {exc}",
                                "text_completeness": None, "page_coverage": None,
                                "latency_s": None, "long_enough": False})
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                state = "ERR " if row.get("error") else f"{(row.get('text_completeness') or 0):.2f}"
                print(f"{parser:14} {case['case_id']:24} {state} {row.get('latency_s')}")

    summary = summarise(rows)
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"\nraw     -> {out_path}")
    print(f"summary -> {summary_path}")
    for s in summary:
        print(f"{s['parser']:14} {s['doc_type']:16} n={s['cases']:2} "
              f"text={s['text_completeness']} table={s['table_accuracy']} "
              f"order={s['reading_order_accuracy']} p50={s['latency_p50_s']}s "
              f"fail={s['failures']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
