#!/usr/bin/env python3
"""End-to-end pipelines: fetch a PDF over HTTP, then parse it.

Three pipelines, same documents, same gold:

  P1  HTTP download  -> PyMuPDF       (the cheap baseline)
  P2  HTTP download  -> MinerU        (GPU parser, no browser)
  P3  Crawl4AI fetch -> MinerU        (what a "browser gets it, GPU reads it"
                                       design would need)

P3 exists to be measured, not to be assumed. Crawl4AI is known to raise on a
PDF URL rather than return bytes; if it fails here it fails as a pipeline, and
we record that instead of quietly substituting the HTTP download.

Success means the pipeline delivered parse text that hits the document's gold
markers. An HTTP 200 with empty text is not success.

Usage:
    python -m benchmark.local_runtime.joint_pipeline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOC_DIR = ROOT / "benchmark/datasets/local-runtime/document"
CASES = DOC_DIR / "cases.jsonl"
BLOB_DIR = DOC_DIR / "blobs/joint"
RUNS_DIR = ROOT / "benchmark/local-runtime/runs"
FETCH_BASE = "http://127.0.0.1:18081"
FETCH_TIMEOUT = 25
PUBLIC_DIR = pathlib.Path("/data/home/fjd/marketops-lab/samples/public")
PUBLIC_BASE = "http://127.0.0.1:18082"


def load_pdf_cases() -> list[dict]:
    rows = [json.loads(l) for l in CASES.read_text().splitlines() if l.strip()]
    return [c for c in rows if c["doc_type"].startswith("pdf")]


def public_cases() -> list[dict]:
    """Real public PDFs scored on delivery, not on markers.

    There is no gold text for a downloaded annual report, so success means the
    pipeline returned a parse that covers every page and has real content.
    """
    cases = []
    for path in sorted(PUBLIC_DIR.glob("*.pdf")):
        raw = path.read_bytes()
        cases.append({
            "case_id": f"PUB-{path.stem.upper()}",
            "doc_type": "pdf-public",
            "difficulty": "real",
            "path": str(path),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "timeout_seconds": 2400,
            "find_tables": False,
            "expected": {"text_markers": [], "min_text_chars": 100},
        })
    return cases


def http_download(url: str) -> dict:
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:
            body = resp.read()
            status = resp.status
    except Exception as exc:
        return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {exc}",
                "bytes": 0, "sha256": None,
                "latency_s": round(time.monotonic() - started, 3)}
    return {"ok": status == 200, "status": status, "error": None,
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "body": body, "latency_s": round(time.monotonic() - started, 3)}


def crawl4ai_fetch(url: str) -> dict:
    from benchmark.local_runtime.fetch_bench import fetch_crawl4ai_local

    try:
        result = fetch_crawl4ai_local({"url": url, "timeout_seconds": FETCH_TIMEOUT})
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "text_chars": 0,
                "latency_s": None}
    text = result.get("text") or ""
    return {"ok": bool(result.get("status") == 200 and text.strip()),
            "status": result.get("status"), "error": result.get("error"),
            "text_chars": len(text), "latency_s": result.get("latency_s")}


def parse_safe(fn, parse_case: dict, gold_case: dict) -> dict:
    from benchmark.local_runtime.document_bench import score

    started = time.monotonic()
    try:
        result = fn(parse_case)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.monotonic() - started, 3)}
    scored = score(gold_case, result)
    return {"ok": True, "error": None, "text_chars": len(result["text"]),
            "text_completeness": scored["text_completeness"],
            "missing_markers": scored["missing_markers"],
            "page_coverage": scored["page_coverage"],
            "latency_s": result["latency_s"], "meta": result["meta"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", action="store_true",
                    help="use the downloaded public PDFs instead of the synthetic set")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from benchmark.local_runtime.document_bench import parse_mineru, parse_pymupdf, score

    BLOB_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    cases = public_cases() if args.public else load_pdf_cases()
    base = PUBLIC_BASE if args.public else FETCH_BASE
    stamp = time.strftime("%Y%m%dT%H%M%S")
    prefix = "joint-public" if args.public else "joint-pipeline"
    out_path = pathlib.Path(args.out) if args.out else RUNS_DIR / f"{prefix}-{stamp}.jsonl"

    rows = []
    with out_path.open("w", encoding="utf-8") as fh:
        for case in cases:
            name = pathlib.Path(case["path"]).name
            url = f"{base}/{name}"
            row = {"case_id": case["case_id"], "doc_type": case["doc_type"],
                   "url": url}

            # Stage 1a: HTTP download. Always performed: it is the control, and
            # P1/P2 depend on it.
            http = http_download(url)
            row["http"] = {k: v for k, v in http.items() if k != "body"}

            # Stage 1b: Crawl4AI. Independent of HTTP; success or failure here
            # is the point of P3.
            crawl = crawl4ai_fetch(url)
            row["crawl4ai"] = crawl

            # A local copy for the GPU parser: MinerU reads a path, not a URL.
            blob = BLOB_DIR / name
            if http["ok"]:
                blob.write_bytes(http["body"])
                row["blob_sha256_matches_source"] = (
                    hashlib.sha256(blob.read_bytes()).hexdigest() == case["sha256"]
                )
            else:
                row["blob_sha256_matches_source"] = None

            # P1: HTTP -> PyMuPDF, on the original file (same bytes as the blob).
            p1 = parse_safe(parse_pymupdf, case, case)

            # P2: HTTP -> MinerU.
            p2 = parse_safe(parse_mineru, {"path": str(blob.relative_to(DOC_DIR)),
                                           "timeout_seconds": case["timeout_seconds"]}, case)

            row["p1_http_pymupdf"] = p1
            row["p2_http_mineru"] = p2

            meta2 = p2.get("meta") or {}
            row["p2_pages"] = {
                "expected": meta2.get("doc_page_count"),
                "covered": len(meta2.get("pages_covered") or []),
                "gaps": meta2.get("coverage_gaps"),
                "content_calls": len(meta2.get("content_calls") or []),
            }
            row["p1_delivered"] = bool(p1["ok"] and (p1.get("text_chars") or 0) >= 100)
            row["p2_delivered"] = bool(
                p2["ok"] and (p2.get("text_chars") or 0) >= 100
                and not (meta2.get("coverage_gaps") or []))
            # P3: Crawl4AI -> MinerU. If the browser step failed there is no
            # document to parse, so the pipeline fails at the first stage.
            row["p3_crawl4ai_mineru"] = {
                "ok": bool(crawl["ok"] and p2["ok"]),
                "failed_stage": None if (crawl["ok"] and p2["ok"]) else (
                    "fetch" if not crawl["ok"] else "parse"),
                "latency_s": round(sum(x for x in (crawl.get("latency_s"), p2.get("latency_s"))
                                       if isinstance(x, (int, float))), 3),
                "text_completeness": p2.get("text_completeness") if crawl["ok"] else None,
            }
            rows.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{case['case_id']:22} http={http['ok']!s:5} crawl4ai={crawl['ok']!s:5} "
                  f"pymupdf={p1['ok']!s:5}({p1.get('text_completeness')}) "
                  f"mineru={p2['ok']!s:5}({p2.get('text_completeness')}) "
                  f"pages={row['p2_pages']['covered']}/{row['p2_pages']['expected']}")

    def agg(key: str) -> dict:
        items = [r[key] for r in rows]
        oks = [i for i in items if i.get("ok")]
        lat = [i["latency_s"] for i in items if isinstance(i.get("latency_s"), (int, float))]
        comp = [i["text_completeness"] for i in oks
                if isinstance(i.get("text_completeness"), (int, float))]
        return {
            "cases": len(items),
            "success": len(oks),
            "success_rate": round(len(oks) / len(items), 4) if items else None,
            "mean_text_completeness": round(statistics.mean(comp), 4) if comp else None,
            "p50_latency_s": round(statistics.median(lat), 3) if lat else None,
            "total_latency_s": round(sum(lat), 3) if lat else None,
        }

    summary = {
        "cases": len(rows),
        "http_download": agg("http"),
        "crawl4ai_fetch": agg("crawl4ai"),
        "p1_http_pymupdf": agg("p1_http_pymupdf"),
        "p2_http_mineru": agg("p2_http_mineru"),
        "p3_crawl4ai_mineru": agg("p3_crawl4ai_mineru"),
        "p3_failed_at_fetch": sum(1 for r in rows
                                  if r["p3_crawl4ai_mineru"]["failed_stage"] == "fetch"),
        "p3_failed_at_parse": sum(1 for r in rows
                                  if r["p3_crawl4ai_mineru"]["failed_stage"] == "parse"),
        "blob_hash_matches_source": sum(1 for r in rows if r["blob_sha256_matches_source"]),
        "p1_delivered": sum(1 for r in rows if r["p1_delivered"]),
        "p2_delivered": sum(1 for r in rows if r["p2_delivered"]),
        "p2_pages_covered": sum(r["p2_pages"]["covered"] or 0 for r in rows),
        "p2_pages_expected": sum(r["p2_pages"]["expected"] or 0 for r in rows),
        "cost_usd": 0.0,
    }
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nraw     -> {out_path}\nsummary -> {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
