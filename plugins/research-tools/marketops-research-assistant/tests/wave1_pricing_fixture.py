"""An offline price catalog for tests that are not about pricing.

The shared ledger refuses an **unpriced** capability rather than estimating it
(``research_usage_ledger``: "A price is recorded, never assumed"). That is the
right default — a made-up price is a pricing authority nobody reviewed — but it
means every test that exercises the *reserve* path needs a catalog, even when the
thing under test is pagination, redaction or a challenge page.

So this module supplies one, and it is deliberately unflattering:

* ``source`` says ``TEST_FIXTURE_NOT_A_VENDOR_RATE``, which is what it is. The
  value is carried into every operation row and into every report, so a test
  artifact can never be mistaken for a researched rate.
* ``verified=False`` records that nobody confirmed these numbers against an
  account. The field exists for exactly this distinction.
* the rates are round and obviously synthetic.

**These are not vendor prices and must not be shipped as one.** Production gets
its catalog from the operator via ``MARKETOPS_USAGE_PRICING``; with none, the
ledger refuses, and that is the intended product behaviour rather than a gap.

The installation patches the module-level resolver rather than setting the
environment variable, because several callers in this suite run inside
``patch.dict(os.environ, ..., clear=True)`` and would lose an env var mid-test.
B's own suites cover the environment path; these tests are about everything else.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

SOURCE = "TEST_FIXTURE_NOT_A_VENDOR_RATE"
VERSION = "fixture-1"
OBSERVED_AT = "2026-10-17T00:00:00+00:00"

#: provider, capability, micro-USD per unit, quota unit, quota per call.
_RATES = (
    ("serper", "search", 1000, "credits", 1),
    ("serpapi", "search", 1000, "searches", 1),
    ("tinyfish", "search", 0, "searches", 1),
    ("tinyfish", "fetch", 0, "urls", 1),
    ("tinyfish", "agent_run", 10000, "steps", 1),
    ("tinyfish", "usage_read", 0, "requests", 1),
    ("tinyfish", "wallet_read", 0, "requests", 1),
    ("crawl4ai", "render", 0, "pages", 1),
    ("crawl4ai", "extract", 0, "pages", 1),
    ("typesafe_jev", "judge", 2000, "requests", 1),
)


def entries() -> list[dict]:
    return [
        {"provider": provider, "capability": capability,
         "unit_micros": micros, "quota_unit": quota_unit, "quota_per_call": per_call,
         "source": SOURCE, "version": VERSION, "observed_at": OBSERVED_AT,
         "verified": False, "free_tier": micros == 0}
        for provider, capability, micros, quota_unit, per_call in _RATES
    ]


def catalog():
    import research_usage_ledger as ledger

    return ledger.PricingCatalog.from_entries(entries())


def write(directory: str | Path | None = None) -> Path:
    """Write the fixture as a JSON config, for a caller that wants the file form."""
    folder = Path(directory) if directory else Path(tempfile.mkdtemp(prefix="wave1-pricing-"))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / "pricing-fixture.json"
    path.write_text(json.dumps({"prices": entries()}, indent=2) + "\n", encoding="utf-8")
    return path


def install() -> None:
    """Give every ledger built in this process a fixture **fallback** catalog.

    It replaces only the fallback, not the precedence: an explicitly configured
    operator catalog still wins. The first version replaced the resolver outright,
    and that made a test asserting "the factory uses the operator catalog" read the
    fixture instead — a fixture that changes an answer rather than supplying a
    missing one turns another module's assertion into a lie, and the failure lands
    in their file.
    """
    import research_usage_ledger as ledger

    real = ledger._pricing_from_environment

    def with_fixture_fallback():
        configured = real()
        return configured if len(configured) else catalog()

    ledger._pricing_from_environment = with_fixture_fallback


def install_env() -> None:
    """The file-and-environment form, for a caller that wants that path exercised."""
    os.environ["MARKETOPS_USAGE_PRICING"] = str(write())
