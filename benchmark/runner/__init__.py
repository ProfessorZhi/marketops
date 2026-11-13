"""MarketOps Provider Evaluation Framework (benchmark v1).

This package is deliberately independent of the production task scheduler: it can be
imported, tested and run offline against recorded provider outputs, and it never touches
network by default.

Entry points:
    metrics  -- deterministic metric functions (no I/O)
    models   -- case/result dataclasses and dataset loading
    judge    -- blind payload construction and judge calibration
    report   -- scorecard -> markdown/json rendering
    cli      -- `python -m benchmark.runner.cli ...`
"""

__version__ = "1.0.0"
SCHEMA_VERSION = "marketops.benchmark.v1"
