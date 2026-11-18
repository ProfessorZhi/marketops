#!/usr/bin/env python3
"""OCR on real page images, where the text layer has been removed.

The synthetic scanned set proves MinerU can read a scan at all. It does not
prove it can read a *real* form: the synthetic pages are generated with known
markers, and a marker that is present is not the same as a line of a tax form
being transcribed correctly.

This builds an image-only PDF from pages of a real public document — the same
layout, fonts, rules and checkboxes, rasterised at 200 dpi into pages with no
text layer — and compares both parsers on it. It is a simulation of a scan,
not a scan: the source was born digital, so the images are clean and there is
no skew, staple shadow or JPEG mush. Calling it a scan would overstate it.

Gold is the source page's own text. Scores:

  * ``token_recall``  -- share of the source's words (4+ chars) found in the
    parser's output. Misses are OCR errors or lost regions.
  * ``similarity``    -- difflib ratio over normalised character strings. A
    blunt measure, but it falls when words are mangled rather than missing.
  * ``pymupdf_chars`` -- must be ~0, which is what makes the page a scan.

Usage:
    python -m benchmark.local_runtime.ocr_real \
        --pdf /data/.../public/irs-1040.pdf --pages 1-2 --dpi 200
"""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import re
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "benchmark/local-runtime/runs"
SCAN_DIR = pathlib.Path("/data/home/fjd/marketops-lab/samples/scan-sim")

WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’./-]{3,}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def rasterise(src: pathlib.Path, pages: str, dpi: int, out: pathlib.Path) -> dict:
    """Render the chosen pages to images and assemble a PDF with no text layer."""
    import pymupdf

    first, _, last = pages.partition("-")
    lo, hi = int(first), int(last or first)
    doc = pymupdf.open(src)
    hi = min(hi, doc.page_count)
    images = []
    for i in range(lo - 1, hi):
        pix = doc[i].get_pixmap(dpi=dpi)
        images.append(pix.tobytes("png"))
    gold = "\n".join(doc[i].get_text() for i in range(lo - 1, hi))
    new = pymupdf.open()
    for png in images:
        page = new.new_page(width=doc[0].rect.width, height=doc[0].rect.height)
        page.insert_image(page.rect, stream=png)
    out.parent.mkdir(parents=True, exist_ok=True)
    new.save(out)
    return {"pages": hi - lo + 1, "gold": gold, "dpi": dpi,
            "raster_bytes": out.stat().st_size}


def score(gold: str, text: str) -> dict:
    g_norm, t_norm = _norm(gold), _norm(text)
    gold_words = set(WORD_RE.findall(g_norm))
    hit = [w for w in gold_words if w in t_norm]
    return {
        "gold_words": len(gold_words),
        "words_hit": len(hit),
        "token_recall": round(len(hit) / len(gold_words), 4) if gold_words else None,
        "similarity": round(difflib.SequenceMatcher(None, g_norm, t_norm).ratio(), 4),
        "text_chars": len(text),
        # A number without evidence is not checkable later: keep what was
        # missed and what the parser actually returned.
        "missing_sample": sorted(gold_words - set(hit))[:20],
        "text_excerpt": text[:400],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", nargs="+", required=True)
    ap.add_argument("--pages", default="1-2")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    from benchmark.local_runtime.document_bench import parse_mineru, parse_pymupdf

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_path = pathlib.Path(args.out) if args.out else RUNS_DIR / f"ocr-real-{stamp}.json"

    rows = []
    for raw in args.pdf:
        src = pathlib.Path(raw)
        # dpi in the name: doclib keys documents by path, so reusing a path for
        # a different raster risks measuring a cached parse of the old one.
        scan = SCAN_DIR / f"{src.stem}-scan-p{args.pages}-{args.dpi}dpi.pdf"
        meta = rasterise(src, args.pages, args.dpi, scan)
        gold = meta.pop("gold")

        started = time.monotonic()
        p1 = parse_pymupdf({"path": str(scan), "find_tables": False})
        p1_s = score(gold, p1["text"])
        p1_s["latency_s"] = p1["latency_s"]

        started = time.monotonic()
        p2 = parse_mineru({"path": str(scan), "timeout_seconds": 600})
        p2_s = score(gold, p2["text"])
        p2_s["latency_s"] = p2["latency_s"]
        p2_s["pages_covered"] = len(p2["meta"].get("pages_covered") or [])
        p2_s["page_count"] = p2["meta"].get("doc_page_count")

        row = {"doc": src.name, "pages": args.pages, **meta,
               "pymupdf": p1_s, "mineru": p2_s}
        rows.append(row)
        print(f"{src.name:24} p{args.pages}  "
              f"pymupdf chars={p1_s['text_chars']:6}  "
              f"mineru recall={p2_s['token_recall']} sim={p2_s['similarity']} "
              f"{p2_s['latency_s']}s")

    summary = {
        "docs": len(rows),
        "dpi": args.dpi,
        "pymupdf_text_chars_total": sum(r["pymupdf"]["text_chars"] for r in rows),
        "mineru_token_recall_mean": round(
            statistics.mean(r["mineru"]["token_recall"] for r in rows), 4) if rows else None,
        "mineru_similarity_mean": round(
            statistics.mean(r["mineru"]["similarity"] for r in rows), 4) if rows else None,
        "mineru_latency_total_s": round(
            sum(r["mineru"]["latency_s"] for r in rows), 3),
        "cost_usd": 0.0,
    }
    out_path.write_text(json.dumps({"summary": summary, "rows": rows},
                                   indent=2, ensure_ascii=False) + "\n")
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nraw -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
