#!/usr/bin/env python3
"""Offline comparison of two saved search discovery receipts; no provider calls."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

SCHEMA = "marketops.search.discovery.v1"


def _normalize_url(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        p = urlsplit(value)
    except ValueError:
        return ""
    if p.scheme not in {"https", "http"} or not p.netloc:
        return ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(),
                       p.path.rstrip("/") or "/", "", ""))


def _keys(item: dict) -> set[str]:
    keys = set()
    if item.get("place_id"):
        keys.add("place:" + str(item["place_id"]))
    if item.get("data_id"):
        keys.add("mapsdata:" + str(item["data_id"]))
    link = _normalize_url(item.get("url"))
    if link:
        keys.add("url:" + link)
    return keys


def _receipt(path: Path) -> dict:
    if path.stat().st_size > 4_000_000:
        raise ValueError("Input receipt exceeds 4 MB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("Both input files must be genuine MarketOps discovery receipts")
    if data.get("source_status") != "discovered_not_verified" or data.get("business_evidence") is not False:
        raise ValueError("Cannot compare a receipt falsely labelled as verified evidence")
    if data.get("provider") not in {"serper", "serpapi"}:
        raise ValueError("Unknown search provider")
    if not isinstance(data.get("results"), list) or data.get("result_count") != len(data["results"]):
        raise ValueError("Receipt row count doesn't match saved results")
    return data


def compare(first: dict, second: dict) -> dict:
    if first["provider"] == second["provider"]:
        raise ValueError("Compare one Serper and one SerpApi receipt, not the same provider")
    for field in ("query", "country", "language", "mode", "limit"):
        if first.get(field) != second.get(field):
            raise ValueError(f"Not an A/B pair: {field} differs")
    def entries(doc: dict) -> list[set[str]]:
        return [_keys(item) for item in doc["results"] if isinstance(item, dict)]
    a = entries(first)
    b = entries(second)
    # Count distinct matched *items*: repeated URLs/place IDs in one source are not extra entities.
    a_mappable = set().union(*a) if a else set()
    b_mappable = set().union(*b) if b else set()
    def overlap(items: list[set[str]], other: set[str]) -> int:
        return sum(1 for keys in items if keys and keys & other)
    return {
        "status": "DISCOVERY_COMPARISON_ONLY_NOT_QUALITY",
        "query": first["query"],
        "mode": first["mode"],
        "country": first["country"],
        "language": first["language"],
        "results": {first["provider"]: len(a), second["provider"]: len(b)},
        "items_with_shared_url_or_place_id": {
            first["provider"]: overlap(a, b_mappable),
            second["provider"]: overlap(b, a_mappable),
        },
        "unmatchable_without_manual_review": {
            first["provider"]: sum(1 for x in a if not x),
            second["provider"]: sum(1 for x in b if not x),
        },
        "warning": (
            "Common official site URLs can belong to different venues. "
            "Different URL paths/place identifiers can represent the same venue. "
            "This is a discovery overlap hint only; true precision, recall, "
            "verified organizations, legitimate contact routes and cost require "
            "independent source-backed review."
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("receipt1", type=Path)
    p.add_argument("receipt2", type=Path)
    args = p.parse_args()
    try:
        report = compare(_receipt(args.receipt1), _receipt(args.receipt2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INVALID_COMPARISON", "reason": type(exc).__name__}),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
