#!/usr/bin/env python3
"""Search providers behind one interface, scored against hand-labelled gold.

Two rules this file exists to enforce:

  * A provider that is not configured reports ``not_configured``. It does not
    return an empty result list, because an empty list scores as perfectly
    precise and that would be a lie in the summary table.
  * Gold labels come from public facts, not from any provider's output. Copying
    a provider's results into ``relevant_urls`` would make that provider score
    1.0 by construction.

Usage:
    python -m benchmark.local_runtime.search_bench --providers serper
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
CASES = ROOT / "benchmark/datasets/local-runtime/search/cases.jsonl"
RUNS_DIR = ROOT / "benchmark/local-runtime/runs"


class NotConfigured(RuntimeError):
    """Raised when a provider has no usable credential or endpoint."""


# --------------------------------------------------------------------------
# providers: each returns list[{url, title, snippet, rank}]
# --------------------------------------------------------------------------

def search_serper(case: dict) -> list[dict]:
    key = os.environ.get("SERPER_API_KEY", "").strip()
    if not key:
        raise NotConfigured("SERPER_API_KEY is not set")
    body = json.dumps({
        "q": case["query"],
        "gl": (case.get("country_code") or "us").lower(),
        "hl": case.get("language_code") or "en",
        "num": max(case.get("top_k", 10), 10),
    }).encode()
    req = urllib.request.Request(
        "https://google.serper.dev/search", data=body,
        headers={"X-API-KEY": key, "Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    out = []
    for i, item in enumerate(payload.get("organic", []), start=1):
        out.append({"url": item.get("link", ""), "title": item.get("title", ""),
                    "snippet": item.get("snippet", ""), "rank": item.get("position", i)})
    return out


def search_searxng(case: dict) -> list[dict]:
    base = os.environ.get("SEARXNG_BASE_URL", "").strip()
    if not base:
        raise NotConfigured("SEARXNG_BASE_URL is not set (a self-hosted instance is required)")
    query = urllib.parse.urlencode({
        "q": case["query"], "format": "json",
        "language": case.get("language_code") or "en",
    })
    with urllib.request.urlopen(f"{base.rstrip('/')}/search?{query}", timeout=30) as resp:
        payload = json.loads(resp.read())
    # SearXNG merges several upstream engines; an engine can return an entry
    # with no URL. Dropping those keeps one broken upstream from being scored
    # as a miss for every case.
    rows = [r for r in payload.get("results", []) if r.get("url")]
    results = [{"url": r["url"], "title": r.get("title", ""),
                "snippet": r.get("content", ""), "rank": i}
               for i, r in enumerate(rows, start=1)]
    # Return the upstream health alongside the results. A zero-result case and
    # a rate-limited case look identical in the row otherwise, and the
    # difference decides whether the provider failed or the query was hard.
    return {"results": results,
            "meta": {"unresponsive_engines": payload.get("unresponsive_engines", []),
                     "engines_seen": sorted({r.get("engine", "?") for r in results})}}


def search_tinyfish(case: dict) -> list[dict]:
    key = os.environ.get("TINYFISH_API_KEY", "").strip()
    if not key:
        raise NotConfigured("TINYFISH_API_KEY is not set")
    # The request shape is not pinned down in this repo yet. Refusing is the
    # honest state; a guessed endpoint would produce numbers that look like
    # measurements.
    raise NotConfigured("TinyFish adapter not implemented: endpoint contract unverified")


def search_deepseek(case: dict) -> list[dict]:
    key = os.environ.get("DEEPSEEK_SEARCH_API_KEY", "").strip()
    if not key:
        raise NotConfigured("DEEPSEEK_SEARCH_API_KEY is not set")
    raise NotConfigured("DeepSeek Search adapter not implemented: separate from DeepSeek LLM")


def search_serpapi_facebook(case: dict) -> list[dict]:
    key = os.environ.get("SERPAPI_API_KEY", "").strip()
    if not key:
        raise NotConfigured("SERPAPI_API_KEY is not set")
    query = urllib.parse.urlencode({
        "engine": "facebook_profile", "q": case["query"], "api_key": key,
    })
    with urllib.request.urlopen(f"https://serpapi.com/search.json?{query}", timeout=30) as resp:
        payload = json.loads(resp.read())
    items = payload.get("organic_results") or payload.get("results") or []
    return [{"url": r.get("link") or r.get("url", ""), "title": r.get("title", ""),
             "snippet": r.get("snippet", ""), "rank": i}
            for i, r in enumerate(items, start=1)]


PROVIDERS = {
    "serper": search_serper,
    "searxng": search_searxng,
    "tinyfish": search_tinyfish,
    "deepseek_search": search_deepseek,
    "serpapi_facebook": search_serpapi_facebook,
}


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def _norm(url: str) -> str:
    parsed = urllib.parse.urlparse(url.strip().lower())
    host = parsed.netloc[4:] if parsed.netloc.startswith("www.") else parsed.netloc
    # LinkedIn serves the same company page from per-country hostnames
    # (na., ng., mt. …). Left alone they look like distinct URLs and a correct
    # result at rank 1 scores as a miss.
    if host.endswith(".linkedin.com"):
        host = "linkedin.com"
    return host + parsed.path.rstrip("/")


def _domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower().replace("www.", "", 1)


def score(case: dict, results: list[dict]) -> dict:
    """Relevance is judged at domain level, on purpose.

    ``relevant_urls`` names the pages the gold author expects, but search
    engines rarely return the exact path. Scoring only exact URL matches would
    punish every provider for a difference that does not matter to callers
    ("did we find the company's own site"), so a result counts as relevant when
    its normalised URL is listed *or* its domain is one the gold already points
    at. The report states this; it is a wider bar than URL equality and it is
    applied identically to every provider.
    """
    gold_urls = [u["url"] for u in case["gold_label"]["relevant_urls"]]
    gold = {_norm(u) for u in gold_urls}
    # Domain-level matching only applies to gold entries that name a site root.
    # A gold like linkedin.com/company/siemens names a specific page; widening
    # it to "any LinkedIn result" would score every competitor's profile as a
    # hit.
    gold_domains = {
        _domain(u) for u in gold_urls
        if urllib.parse.urlparse(u).path.strip("/") == ""
    }
    forbidden = {_norm(u) for u in case["gold_label"].get("forbidden_urls", [])}

    seen, dup = set(), 0
    urls, item_domains, relevant_flags = [], [], []
    for r in results:
        n = _norm(r["url"])
        d = _domain(r["url"])
        if n in seen:
            dup += 1
        seen.add(n)
        urls.append(n)
        item_domains.append(d)
        relevant_flags.append(n in gold or d in gold_domains)

    def precision_at(k):
        top = relevant_flags[:k]
        return round(sum(top) / k, 4) if k else None

    def recall_at(k):
        if not gold:
            return None
        # The gold here is a set of pages; a domain-level hit satisfies the
        # page it was derived from. Compare against the raw domain per item —
        # deriving it from the normalised URL yields an empty netloc and
        # silently scores every domain hit as a miss.
        hit = sum(1 for u, d in zip(urls[:k], item_domains[:k])
                  if u in gold or d in gold_domains)
        return round(min(hit, len(gold)) / len(gold), 4)

    domains = set(item_domains)
    return {
        "results": len(results),
        "precision_at_5": precision_at(5),
        "precision_at_10": precision_at(10),
        "recall_at_5": recall_at(5),
        "recall_at_10": recall_at(10),
        "domain_hit_rate": round(len(domains & gold_domains) / len(gold_domains), 4) if gold_domains else None,
        "unique_relevant_coverage": round(len(domains & gold_domains) / len(gold_domains), 4) if gold_domains else None,
        "duplicate_rate": round(dup / len(results), 4) if results else None,
        "forbidden_hits": len(forbidden & set(urls)),
        "unique_domains": len(domains),
    }


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["provider"], []).append(row)
    out = []
    for provider, items in sorted(groups.items()):
        ran = [i for i in items if i.get("status") == "ok"]

        def mean(key):
            vals = [i[key] for i in ran if i.get(key) is not None]
            return round(statistics.mean(vals), 4) if vals else None

        upstream: dict[str, int] = {}
        for i in ran:
            for engine, why in (i.get("meta") or {}).get("unresponsive_engines", []):
                upstream[f"{engine}: {why}"] = upstream.get(f"{engine}: {why}", 0) + 1

        out.append({
            "provider": provider,
            "cases": len(items),
            "ran": len(ran),
            "not_configured": sum(1 for i in items if i.get("status") == "not_configured"),
            "errors": sum(1 for i in items if i.get("status") == "error"),
            "empty_result_rows": sum(1 for i in ran if i.get("results") == 0),
            "upstream_errors": upstream or None,
            "precision_at_5": mean("precision_at_5"),
            "precision_at_10": mean("precision_at_10"),
            "recall_at_5": mean("recall_at_5"),
            "recall_at_10": mean("recall_at_10"),
            "domain_hit_rate": mean("domain_hit_rate"),
            "unique_relevant_coverage": mean("unique_relevant_coverage"),
            "duplicate_rate": mean("duplicate_rate"),
            "latency_p50_s": (round(statistics.median([i["latency_s"] for i in ran]), 3)
                              if ran else None),
            "cost_usd": round(sum(i.get("cost_usd") or 0 for i in ran), 4) if ran else None,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", nargs="+", default=list(PROVIDERS))
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-interval", type=float, default=0.0,
                    help="seconds to wait between requests; upstreams rate-limit bursts")
    args = ap.parse_args()

    cases_path = pathlib.Path(args.cases)
    if not cases_path.exists():
        print(f"no case file at {cases_path}", file=sys.stderr)
        return 2
    cases = [json.loads(line) for line in cases_path.read_text().splitlines() if line.strip()]

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = pathlib.Path(args.out) if args.out else RUNS_DIR / f"search-bench-{time.strftime('%Y%m%dT%H%M%S')}.jsonl"

    rows = []
    with out_path.open("w", encoding="utf-8") as fh:
        for provider in args.providers:
            fn = PROVIDERS[provider]
            for case in cases:
                if args.min_interval:
                    time.sleep(args.min_interval)
                row = {"provider": provider, "case_id": case["case_id"],
                       "task_type": case["task_type"], "query": case["query"]}
                started = time.monotonic()
                try:
                    out = fn(case)
                    if isinstance(out, dict):
                        results, meta = out["results"], out.get("meta", {})
                    else:
                        results, meta = out, {}
                    row["latency_s"] = round(time.monotonic() - started, 3)
                    row["status"] = "ok"
                    row["meta"] = meta
                    row.update(score(case, results))
                except NotConfigured as exc:
                    row.update({"status": "not_configured", "detail": str(exc),
                                "latency_s": None})
                except Exception as exc:
                    row.update({"status": "error", "detail": f"{type(exc).__name__}: {exc}",
                                "latency_s": None})
                rows.append(row)
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                print(f"{provider:18} {case['case_id']:28} {row['status']}")

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
