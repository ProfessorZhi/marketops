#!/usr/bin/env python3
"""Fetch providers on an identical URL set, scored against fixture gold.

Every provider receives the same ``case["url"]``. There is no per-provider URL
list: a provider that only works on sites that suit it is not comparable.

Two things this deliberately does not do:

  * It does not treat HTTP 200 as success. A shell page answers 200 with an
    empty container; ``expected_outcome: no_content`` exists to catch exactly
    that, and a reader that returns the shell scores as a false success.
  * It does not give a provider a lower bar. ``min_text_chars`` and the marker
    set come from the dataset, not from the provider.

Usage:
    python -m benchmark.local_runtime.fetch_bench --providers http_trafilatura
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "benchmark/datasets/local-runtime/fetch/fixtures.jsonl"
RUNS_DIR = ROOT / "benchmark/local-runtime/runs"


# --------------------------------------------------------------------------
# providers: each returns dict(status, final_url, text, error, latency_s, meta)
# --------------------------------------------------------------------------

def fetch_http_trafilatura(case: dict) -> dict:
    import httpx
    import trafilatura

    started = time.monotonic()
    try:
        resp = httpx.get(
            case["url"],
            follow_redirects=True,
            timeout=case.get("timeout_seconds", 15),
            headers={"User-Agent": "MarketOpsBenchmark/1.0 (+research)"},
        )
    except Exception as exc:
        return {"status": None, "final_url": case["url"], "text": "",
                "error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.monotonic() - started, 3), "meta": {}}

    final_url = str(resp.url)
    text = ""
    # trafilatura reads HTML; a PDF served over HTTP is handed to its own
    # extractor only if the content type says so.
    if "pdf" in resp.headers.get("content-type", ""):
        text = ""
    else:
        text = trafilatura.extract(
            resp.text, url=final_url, include_comments=False,
            include_tables=True, favor_recall=True,
        ) or ""
    return {
        "status": resp.status_code,
        "final_url": final_url,
        "text": text,
        "error": None,
        "latency_s": round(time.monotonic() - started, 3),
        "meta": {"engine": "httpx+trafilatura", "bytes": len(resp.content),
                 "redirects": len(resp.history)},
    }


def _crawl4ai_token() -> str:
    env_file = ROOT / "services/crawl4ai-local/.env"
    for line in env_file.read_text().splitlines():
        if line.startswith("CRAWL4AI_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("CRAWL4AI_API_TOKEN not found")


def fetch_crawl4ai_local(case: dict) -> dict:
    """Call the local REST API, not the library.

    Calling the library would skip the service layer the benchmark claims to
    have deployed, and would let a stale in-process browser state pass a case
    the API cannot.
    """
    import httpx

    started = time.monotonic()
    try:
        resp = httpx.post(
            "http://127.0.0.1:11235/crawl",
            json={"urls": [case["url"]]},
            headers={"Authorization": f"Bearer {_crawl4ai_token()}"},
            timeout=case.get("timeout_seconds", 15) + 60,
        )
    except Exception as exc:
        return {"status": None, "final_url": case["url"], "text": "",
                "error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.monotonic() - started, 3), "meta": {}}

    latency = round(time.monotonic() - started, 3)
    if resp.status_code != 200:
        return {"status": resp.status_code, "final_url": case["url"], "text": "",
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                "latency_s": latency, "meta": {}}
    payload = resp.json()
    item = (payload.get("results") or [{}])[0]
    # A failed crawl still answers 200 with success=false; that is not content.
    if not item.get("success"):
        return {"status": item.get("status_code"), "final_url": item.get("final_url") or case["url"],
                "text": "", "error": item.get("error") or "crawl reported success=false",
                "latency_s": latency, "meta": {"engine": "crawl4ai-local-api"}}
    return {
        "status": item.get("status_code"),
        "final_url": item.get("final_url") or case["url"],
        "text": item.get("markdown") or "",
        "error": None,
        "latency_s": latency,
        "meta": {"engine": "crawl4ai-local-api", "server_elapsed_s": item.get("elapsed_s")},
    }


PROVIDERS = {
    "http_trafilatura": fetch_http_trafilatura,
    "crawl4ai_local": fetch_crawl4ai_local,
}


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def evaluate(case: dict, result: dict) -> dict:
    gold = case["gold_label"]
    text = result["text"] or ""
    markers = list(gold.get("expected_text_markers", []))
    markers += gold.get("expected_emails", [])
    markers += gold.get("expected_phones", [])
    markers += [p["name"] for p in gold.get("expected_persons", [])]
    hits = [m for m in markers if m in text]
    completeness = round(len(hits) / len(markers), 4) if markers else None
    forbidden = [s for s in gold.get("forbidden_strings", []) if s in text]

    if case["expected_outcome"] == "no_content":
        # Correct here means saying "nothing usable", not saying 200.
        # A shell page often yields a few dozen characters of boilerplate
        # ("enable JavaScript"), so length alone cannot decide: a forbidden
        # string in the output means the reader handed back the shell.
        shell_text = any(s in text for s in gold.get("forbidden_strings", []))
        useful = len(text.strip()) < 80 and not shell_text
        return {
            "useful_content": useful,
            "false_success": bool(not useful),
            "shell_text_returned": shell_text,
            "text_completeness": completeness,
            "contact_ok": None,
            "http_success": result["status"] == 200,
            "final_url_ok": None,
            "error": result["error"],
        }

    useful = len(text) >= gold.get("min_text_chars", 0) and (completeness or 0) >= 0.6
    expected_final = case.get("final_url_expected")
    final_ok = (result["final_url"] == expected_final) if expected_final else None
    contacts = gold.get("expected_emails", []) + gold.get("expected_phones", [])
    contact_hits = [c for c in contacts if c in text]
    return {
        "useful_content": useful,
        "false_success": False,
        "text_completeness": completeness,
        "contact_ok": (len(contact_hits) == len(contacts)) if contacts else None,
        "contact_completeness": round(len(contact_hits) / len(contacts), 4) if contacts else None,
        "http_success": result["status"] == 200,
        "final_url_ok": final_ok,
        "missing_markers": [m for m in markers if m not in text],
        "forbidden_hits": forbidden,
        "error": result["error"],
    }


def summarise(rows: list[dict]) -> list[dict]:
    by_provider: dict[str, list[dict]] = {}
    for row in rows:
        by_provider.setdefault(row["provider"], []).append(row)
    out = []
    for provider, items in sorted(by_provider.items()):
        content_items = [i for i in items if i["expected_outcome"] == "content"]
        nocontent = [i for i in items if i["expected_outcome"] == "no_content"]

        def rate(subset, key):
            vals = [i[key] for i in subset if i.get(key) is not None]
            return round(sum(1 for v in vals if v) / len(vals), 4) if vals else None

        latencies = sorted(i["latency_s"] for i in items if i.get("latency_s") is not None)
        out.append({
            "provider": provider,
            "cases": len(items),
            "useful_content_rate": rate(content_items, "useful_content"),
            "text_completeness_mean": round(statistics.mean(
                [i["text_completeness"] for i in content_items if i["text_completeness"] is not None]
            ), 4) if content_items else None,
            "contact_completeness_mean": round(statistics.mean(
                [i["contact_completeness"] for i in content_items
                 if i.get("contact_completeness") is not None]
            ), 4) if any(i.get("contact_completeness") is not None for i in content_items) else None,
            "http_success_rate": rate(items, "http_success"),
            "final_url_accuracy": rate(content_items, "final_url_ok"),
            "false_success_on_no_content": sum(1 for i in nocontent if i["false_success"]),
            "errors": sum(1 for i in items if i.get("error")),
            "latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
            "latency_p95_s": (round(latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))], 3)
                              if latencies else None),
            "cost_usd": 0.0,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+", default=list(PROVIDERS))
    ap.add_argument("--cases", default=str(FIXTURES))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cases = [json.loads(line) for line in pathlib.Path(args.cases).read_text().splitlines() if line.strip()]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    out_path = pathlib.Path(args.out) if args.out else RUNS_DIR / f"fetch-bench-{stamp}.jsonl"

    rows = []
    with out_path.open("w", encoding="utf-8") as fh:
        for provider in args.providers:
            fn = PROVIDERS[provider]
            for case in cases:
                row = {"provider": provider, "case_id": case["case_id"],
                       "task_type": case["task_type"],
                       "expected_outcome": case["expected_outcome"]}
                try:
                    result = fn(case)
                    row.update({"status": result["status"], "final_url": result["final_url"],
                                "text_chars": len(result["text"] or ""),
                                "excerpt": (result["text"] or "")[:200],
                                "latency_s": result["latency_s"], "meta": result["meta"]})
                    row.update(evaluate(case, result))
                except Exception as exc:
                    row.update({"error": f"{type(exc).__name__}: {exc}",
                                "useful_content": False, "http_success": False,
                                "latency_s": None})
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                state = "ERR" if row.get("error") else (
                    "useful" if row.get("useful_content") else "MISS")
                print(f"{provider:18} {case['case_id']:26} {state:6} "
                      f"{row.get('text_chars', 0):>6} chars {row.get('latency_s')}s")

    summary = summarise(rows)
    summary_path = out_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    print(f"\nraw     -> {out_path}")
    print(f"summary -> {summary_path}")
    for s in summary:
        print(json.dumps(s, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
