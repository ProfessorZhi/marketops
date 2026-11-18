#!/usr/bin/env python3
"""Compare routing strategies using recorded per-case measurements.

This does not call a provider. It reads the raw rows from earlier runs and
applies a routing rule to them, so a strategy's number is the same arithmetic
anyone can redo from the committed JSONL — not a second, unrecorded run.

Two rules are compared per stage:

  * single-tool  -- always use this tool
  * routed       -- use the cheap tool first; escalate when its output is too
                    thin to be a parse (a threshold on characters, which is a
                    signal the production caller actually has)

The escalation threshold is fixed before the numbers are read, and applies to
every case: picking per case would be fitting the rule to the answer.

Usage:
    python -m benchmark.local_runtime.strategy_eval
"""

from __future__ import annotations

import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "benchmark/local-runtime/runs"

FETCH_RUN = "fetch-bench-20261118T194107.jsonl"     # Docker Crawl4AI
DOC_RUN = "document-bench-all.jsonl"
OCR_RUN = "ocr-real-20261118T194826.json"           # IRS at 300 dpi

# Production-visible signals, not gold: below this many characters the caller
# has nothing worth extracting and escalates.
FETCH_MIN_CHARS = 200
DOC_MIN_CHARS = 100


def load(name: str) -> list[dict]:
    return [json.loads(l) for l in (RUNS / name).read_text().splitlines() if l.strip()]


def by_case(rows: list[dict], key: str) -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for row in rows:
        out.setdefault(row["case_id"], {})[row[key]] = row
    return out


def fetch_strategies() -> list[dict]:
    rows = by_case(load(FETCH_RUN), "provider")
    strategies = {
        "F1 http+trafilatura only":
            lambda c: c["http_trafilatura"],
        "F2 crawl4ai only":
            lambda c: c["crawl4ai_local"],
        "F3 http first, browser if thin":
            lambda c: (c["http_trafilatura"]
                       if (c["http_trafilatura"].get("text_chars") or 0) >= FETCH_MIN_CHARS
                       else c["crawl4ai_local"]),
    }
    out = []
    for name, pick in strategies.items():
        chosen = [pick(cases) for cases in rows.values()]
        ok = [r for r in chosen if r.get("useful_content")]
        lat = [r["latency_s"] for r in chosen if isinstance(r.get("latency_s"), (int, float))]
        out.append({
            "stage": "fetch", "strategy": name, "cases": len(chosen),
            "qualified": len(ok),
            "qualified_rate": round(len(ok) / len(chosen), 4),
            "mean_latency_s": round(statistics.mean(lat), 4),
            "total_latency_s": round(sum(lat), 3),
            "false_success": sum(1 for r in chosen if r.get("false_success")),
            "escalations": sum(1 for c in rows.values()
                               if (c["http_trafilatura"].get("text_chars") or 0) < FETCH_MIN_CHARS)
            if "F3" in name else None,
        })
    return out


def _doc_qualified(row: dict) -> bool:
    return (row.get("text_completeness") == 1.0
            and row.get("reading_order_ok") is not False)


def doc_strategies() -> list[dict]:
    rows = {cid: by_parser for cid, by_parser in by_case(load(DOC_RUN), "parser").items()
            # Office cases have native rows only; a PDF parser is not an
            # option there, so including them would compare nothing.
            if "pymupdf" in by_parser and "mineru_local" in by_parser}
    strategies = {
        "D1 pymupdf only": lambda c: c["pymupdf"],
        "D2 mineru only": lambda c: c["mineru_local"],
        "D3 pymupdf first, mineru if thin":
            lambda c: (c["pymupdf"] if (c["pymupdf"].get("text_chars") or 0) >= DOC_MIN_CHARS
                       else c["mineru_local"]),
    }
    out = []
    for name, pick in strategies.items():
        chosen = [pick(cases) for cases in rows.values()]
        lat = [r["latency_s"] for r in chosen if isinstance(r.get("latency_s"), (int, float))]
        out.append({
            "stage": "document", "strategy": name, "cases": len(chosen),
            "qualified": sum(1 for r in chosen if _doc_qualified(r)),
            "qualified_rate": round(sum(1 for r in chosen if _doc_qualified(r)) / len(chosen), 4),
            "mean_latency_s": round(statistics.mean(lat), 4),
            "total_latency_s": round(sum(lat), 3),
            "escalations": sum(1 for c in rows.values()
                               if (c["pymupdf"].get("text_chars") or 0) < DOC_MIN_CHARS)
            if "D3" in name else None,
        })
    return out


def ocr_check() -> dict:
    """The one case where the cheap parser has no answer at all."""
    data = json.loads((RUNS / OCR_RUN).read_text())
    row = data["rows"][0]
    return {"doc": row["doc"], "dpi": data["summary"]["dpi"],
            "pymupdf_chars": row["pymupdf"]["text_chars"],
            "mineru_token_recall": row["mineru"]["token_recall"],
            "mineru_latency_s": row["mineru"]["latency_s"]}


def main() -> int:
    result = {
        "fetch": fetch_strategies(),
        "document": doc_strategies(),
        "ocr": ocr_check(),
        "inputs": {"fetch_run": FETCH_RUN, "doc_run": DOC_RUN, "ocr_run": OCR_RUN,
                   "fetch_min_chars": FETCH_MIN_CHARS, "doc_min_chars": DOC_MIN_CHARS},
        "cost_usd": 0.0,
    }
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = RUNS / f"strategy-eval-{stamp}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    for stage in ("fetch", "document"):
        for s in result[stage]:
            print(f"{stage:8} {s['strategy']:34} qualified={s['qualified']}/{s['cases']} "
                  f"mean={s['mean_latency_s']}s total={s['total_latency_s']}s "
                  f"esc={s['escalations']}")
    print(f"ocr     {result['ocr']}")
    print(f"\nraw -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
