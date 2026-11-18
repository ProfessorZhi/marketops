#!/usr/bin/env python3
"""SearXNG acceptance: 10 query types, and which upstream engines actually work.

SearXNG is a meta search: it owns no index. The engine count in settings.yml is
a list of *definitions*, not a list of working searches. This script therefore
counts, per query, which engine produced results and which ones reported back
with an error, so "20 results" is never mistaken for "20 engines worked".

Usage:
    python3 services/searxng-local/acceptance.py [--base http://127.0.0.1:8888]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

QUERIES = [
    ("corporate_website",   "official website of OpenAI", "en", "us"),
    ("company_executive",   "OpenAI CEO Sam Altman profile", "en", "us"),
    ("company_contact",     "Siemens AG corporate contact email phone", "en", "de"),
    ("youtube_creator",     "MrBeast YouTube channel", "en", "us"),
    ("facebook_page",       "Maersk official Facebook page", "en", "dk"),
    ("country_region",      "government of the Netherlands official portal", "en", "nl"),
    ("competitor_discovery", "alternatives to Salesforce CRM", "en", "us"),
    ("specific_industry",   "EUV lithography equipment manufacturers", "en", "nl"),
    ("multilingual",        "西门子 官方网站", "zh", "cn"),
    ("long_tail",           "ASML EUV lithography machine manufacturer headquarters Veldhoven Netherlands", "en", "nl"),
]

# SearXNG reports upstream trouble in unresponsive_engines as [engine, reason].
# These substrings are the ones that mean "this upstream is not usable right now"
# rather than "this upstream had a transient hiccup".
BLOCK_MARKERS = ("captcha", "too many requests", "429", "403", "forbidden", "access denied")


def run(base: str, query: str, lang: str, country: str) -> dict:
    params = {"q": query, "format": "json", "language": lang}
    if country:
        params["country"] = country
    url = f"{base.rstrip('/')}/search?" + urllib.parse.urlencode(params)
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return {"http_status": exc.code, "error": exc.read().decode(errors="replace")[:300],
                "latency_s": round(time.monotonic() - started, 3)}
    except Exception as exc:
        return {"http_status": None, "error": f"{type(exc).__name__}: {exc}",
                "latency_s": round(time.monotonic() - started, 3)}

    latency = round(time.monotonic() - started, 3)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {"http_status": status, "error": "non-JSON body",
                "latency_s": latency, "body_head": raw[:200].decode(errors="replace")}

    results = payload.get("results", [])
    engines: dict[str, int] = {}
    for r in results:
        engines[r.get("engine", "?")] = engines.get(r.get("engine", "?"), 0) + 1
    unresponsive = payload.get("unresponsive_engines", [])
    blocked = [u for u in unresponsive
               if any(m in str(u[1]).lower() for m in BLOCK_MARKERS)]
    return {
        "http_status": status,
        "latency_s": latency,
        "result_count": len(results),
        "engines": engines,
        "unresponsive_engines": unresponsive,
        "blocked_engines": blocked,
        "top3": [r.get("url", "") for r in results[:3]],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8888")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = []
    for label, query, lang, country in QUERIES:
        row = run(args.base, query, lang, country)
        row.update({"label": label, "query": query, "language": lang, "country": country})
        rows.append(row)
        print(f"{label:20} {row.get('http_status')} {row.get('latency_s'):>6}s "
              f"{row.get('result_count', 0):>3} results  "
              f"engines={row.get('engines')}  blocked={[b[0] for b in row.get('blocked_engines', [])]}")
        time.sleep(1.0)

    # Which upstreams actually produced results, across the whole run.
    produced: dict[str, int] = {}
    failed: dict[str, int] = {}
    reasons: dict[str, str] = {}
    for row in rows:
        for e in row.get("engines") or {}:
            produced[e] = produced.get(e, 0) + 1
        for e, why in row.get("unresponsive_engines") or []:
            failed[e] = failed.get(e, 0) + 1
            reasons.setdefault(e, why)

    summary = {
        "base": args.base,
        "queries": len(rows),
        "engines_that_produced_results": produced,
        "engines_that_errored": failed,
        "error_reasons": reasons,
        "queries_with_no_results": sum(1 for r in rows if not r.get("result_count")),
        "mean_latency_s": round(sum(r["latency_s"] for r in rows) / len(rows), 3),
        "max_latency_s": max(r["latency_s"] for r in rows),
        "blocked_engine_events": sum(len(r.get("blocked_engines") or []) for r in rows),
    }
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))
    if args.out:
        pathlib.Path(args.out).write_text(
            json.dumps({"rows": rows, "summary": summary}, ensure_ascii=False, indent=2) + "\n"
        )
        print(f"written -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
