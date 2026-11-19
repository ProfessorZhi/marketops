"""The Mock Provider contract test: 7 providers x 10 failure modes.

A mock is only useful if it fails the way the real API fails. This module
exercises every provider in every mode and asserts the *status* the runner
records -- not just that something was raised. The three "malformed" modes are
kept apart on purpose:

    bad_format     well-formed JSON, a list field replaced by an object
                   -> invalid_output
    missing_field  well-formed JSON, a required key absent
                   -> invalid_output
    invalid_json   not JSON at all
                   -> failed

and none of them may collapse into ``empty``. A provider that is rate limited or
returning 500 is ``failed``; one that is down is ``timeout``; one with no key is
``not_configured``. ``empty`` means the provider answered, successfully, with
nothing -- which is a real and scoreable answer.

Run directly for the table:

    python -m benchmark.research_core.mocks.contract
"""

from __future__ import annotations

import json
import sys

from .. import runner
from ..adapters import mock

#: mode -> the status the runner must record.
EXPECTED = {
    "normal": "ok",
    "empty": "empty",
    "timeout": "timeout",
    "rate_limited": "failed",
    "server_error": "failed",
    "duplicate": "ok",
    "bad_format": "invalid_output",
    "missing_field": "invalid_output",
    "invalid_json": "failed",
    "multilingual": "ok",
}

SEARCH_CASE = {
    "case_id": "RC-SRCH-CONTRACT-999",
    "stage": "search",
    "input": {
        "query": "marine deck fittings supplier Netherlands",
        "country_code": "NL",
        "language_code": "nl",
        "top_k": 10,
        "intent": "official_website",
    },
    "gold": {},
}

FETCH_CASE = {
    "case_id": "RC-FECH-CONTRACT-999",
    "stage": "fetch",
    "input": {"url": f"{mock.FIXTURE_BASE}/northwind-marine/about.html"},
    "gold": {},
}

SEARCH_PROVIDERS = (
    "mock_serper", "mock_tinyfish_search", "mock_deepseek_search",
    "mock_youtube_data", "mock_google_maps_places",
)
FETCH_PROVIDERS = ("mock_tinyfish_fetch", "mock_deepseek_fetch")


def _probe(provider: str, stage: str, mode: str) -> dict:
    case = SEARCH_CASE if stage == "search" else FETCH_CASE
    adapter = (mock.MockSearchAdapter(provider, mode=mode) if stage == "search"
               else mock.MockFetchAdapter(provider, mode=mode))
    status, error = runner.probe(adapter, stage, case)
    return {
        "provider": provider,
        "stage": stage,
        "mode": mode,
        "expected": EXPECTED[mode],
        "actual": status,
        "ok": status == EXPECTED[mode],
        "error_kind": None if error is None else getattr(error, "kind", type(error).__name__),
    }


def run_matrix() -> list[dict]:
    rows: list[dict] = []
    for provider in SEARCH_PROVIDERS:
        for mode in EXPECTED:
            rows.append(_probe(provider, "search", mode))
    for provider in FETCH_PROVIDERS:
        for mode in EXPECTED:
            rows.append(_probe(provider, "fetch", mode))
    return rows


def failures(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not row["ok"]]


def main() -> int:
    rows = run_matrix()
    for row in rows:
        mark = "ok  " if row["ok"] else "FAIL"
        print(f"{mark} {row['provider']:<24} {row['stage']:<6} {row['mode']:<14} "
              f"expected={row['expected']:<14} actual={row['actual']}")
    bad = failures(rows)
    print(f"\n{len(rows) - len(bad)}/{len(rows)} contract checks passed")
    if bad:
        print(json.dumps(bad, indent=2))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
