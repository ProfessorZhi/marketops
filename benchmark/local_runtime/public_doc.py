#!/usr/bin/env python3
"""Compare PyMuPDF and MinerU on real public PDFs, page by page.

The synthetic set in ``document/`` proves capability boundaries but says
nothing about real layouts. This runner is for downloaded public documents,
where there is no hand-written gold: instead it records properties that a
truncated or scrambled parse cannot fake.

  * page coverage -- how many of the PDF's pages appear in the output. A
    parser that stops early still looks "ok" without this check.
  * section hits -- the last-section markers (References, Appendix, ...). A
    parse that silently stops at page 8 loses the bibliography entirely.
  * structure -- headings, LaTeX blocks, table markup.

Usage:
    python -m benchmark.local_runtime.public_doc --pdf samples/public/arxiv-attention.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS = ROOT / "benchmark/local-runtime/runs"
PUBLIC = pathlib.Path("/data/home/fjd/marketops-lab/samples/public")

PAGE_MARKER_RE = re.compile(r"<!-- page (\d+) of (\d+) -->")
HEADING_RE = re.compile(r"^#{1,6} \S", re.M)


def _probe(text: str, pages_expected: int | None) -> dict:
    markers = [(int(a), int(b)) for a, b in PAGE_MARKER_RE.findall(text)]
    covered = sorted({p for p, _ in markers})
    total = markers[0][1] if markers else pages_expected
    return {
        "chars": len(text),
        "page_markers": len(markers),
        "pages_covered": covered,
        "pages_expected": total,
        "coverage_gaps": sorted(set(range(1, (pages_expected or total or 0) + 1)) - set(covered)),
        "headings": len(HEADING_RE.findall(text)),
        "latex_blocks": text.count("$$"),
        "table_tags": text.count("<table"),
        "references_hits": text.count("References"),
        "bib_entries": len(re.findall(r"\[[0-9]{1,3}\]", text)),
    }


def run_pymupdf(path: pathlib.Path, want_tables: bool) -> dict:
    import pymupdf

    started = time.monotonic()
    doc = pymupdf.open(path)
    parts, tables = [], 0
    for page in doc:
        parts.append(page.get_text())
        # find_tables() is opt-in: it is a structural analysis that costs
        # seconds per page on a dense document (a 492-page standard spent
        # >10 CPU-minutes in it before this was made a flag). Page text is
        # what this comparison is about; table counts are not comparable
        # with MinerU's markdown anyway.
        if want_tables:
            try:
                tables += len(page.find_tables().tables)
            except Exception:
                pass
    text = "".join(parts)
    probe = _probe(text, doc.page_count)
    probe["pages"] = doc.page_count
    probe["tables_detected"] = tables
    return {"ok": True, "error": None, "latency_s": round(time.monotonic() - started, 3),
            "probe": probe, "text": text}


def run_mineru(path: pathlib.Path, timeout_s: float) -> dict:
    from benchmark.local_runtime.document_bench import parse_mineru

    started = time.monotonic()
    try:
        result = parse_mineru({"path": str(path), "timeout_seconds": timeout_s})
    except Exception as exc:  # noqa: BLE001 - recorded as a failure, not swallowed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.monotonic() - started, 3)}
    meta = result["meta"]
    probe = _probe(result["text"], meta.get("doc_page_count"))
    probe["pages"] = result["pages"]
    probe["tables_detected"] = result["tables_detected"]
    return {"ok": True, "error": None, "latency_s": result["latency_s"],
            "probe": probe, "meta": meta, "text": result["text"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", nargs="+", default=None)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--save-text", action="store_true",
                    help="keep full parsed text next to the run file")
    ap.add_argument("--tables", action="store_true",
                    help="also count PyMuPDF find_tables() results (slow on long PDFs)")
    args = ap.parse_args()

    pdfs = [pathlib.Path(p) for p in (args.pdf or sorted(PUBLIC.glob("*.pdf")))]
    if not pdfs:
        print(f"no PDFs under {PUBLIC}", file=sys.stderr)
        return 2

    records = []
    for pdf in pdfs:
        raw = pdf.read_bytes()
        rec = {
            "file": str(pdf),
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "pymupdf": run_pymupdf(pdf, want_tables=args.tables),
            "mineru": run_mineru(pdf, args.timeout),
        }
        for name in ("pymupdf", "mineru"):
            if not args.save_text:
                rec[name].pop("text", None)
        records.append(rec)

    stamp = time.strftime("%Y%m%dT%H%M%S")
    out = pathlib.Path(args.out) if args.out else RUNS / f"public-doc-{stamp}.json"
    out.write_text(json.dumps(records, indent=1, ensure_ascii=False) + "\n")

    for rec in records:
        print(f"\n== {pathlib.Path(rec['file']).name}  {rec['bytes']} B  {rec['sha256'][:12]}")
        for name in ("pymupdf", "mineru"):
            r = rec[name]
            if not r["ok"]:
                print(f"  {name:8s} FAILED  {r['error']}")
                continue
            p = r["probe"]
            print(f"  {name:8s} {r['latency_s']:7.2f}s  chars={p['chars']:6d}  "
                  f"pages={len(p['pages_covered']) or p['pages']}/{p['pages_expected']}  "
                  f"gaps={p['coverage_gaps'][:5] or '-'}  "
                  f"headings={p['headings']}  bib={p['bib_entries']}")
    print(f"\nwritten -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
