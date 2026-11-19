# Research Core Local Benchmark Lab.
#
# Every target here is offline and needs no API key. The one exception is
# `research-public-capture`, which is the explicit, one-time act of freezing
# real public snapshots; it is never part of a test run.

PY ?= .venv/bin/python
PYTEST := $(PY) -m pytest -o addopts= -q
RC := $(PY) -m benchmark.research_core.cli
CORE_TESTS := tests/test_research_datasets.py tests/test_research_core_benchmark.py

PUBLIC := benchmark/datasets/research-core/public-evidence

.PHONY: research-test-env research-test research-test-offline research-benchmark \
        research-dataset-validate research-report research-public-benchmark \
        research-public-capture research-contract research-fixture-server

## Check the environment the lab runs in: adapters, fixture server, schema.
research-test-env:
	$(RC) list
	$(RC) validate
	$(RC) contract

## Schema, determinism, adapter contract and the reference suite.
research-test:
	$(PYTEST) $(CORE_TESTS)

## The same suite with no network at all. Proves a run needs nothing but bytes.
research-test-offline:
	HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY='*' \
		$(PYTEST) $(CORE_TESTS)

## Run all nine stages three times and assert the gold metrics do not move.
research-benchmark:
	$(RC) suite --repeat 3

## Schema-check every dataset in every stage it holds.
research-dataset-validate:
	$(RC) validate --also $(PUBLIC)

## Public Evidence Benchmark: real snapshots, frozen bytes, offline scoring.
research-public-benchmark:
	$(RC) --dataset-root $(PUBLIC) --out-dir benchmark/research-core/runs \
		suite --stages fetch evidence --tag=-public --repeat 3

## Render the Markdown report from the last suite run.
research-report:
	$(RC) report --with-public --out benchmark/research-core/RESEARCH_CORE_TEST_REPORT.md

## The mock provider matrix on its own (7 providers x 10 failure modes).
research-contract:
	$(RC) contract

## Serve the synthetic corpus and frozen public snapshots for manual poking.
research-fixture-server:
	$(PY) -m benchmark.research_core.mocks.web --port 18090 &
	$(PY) -m benchmark.research_core.mocks.public_web --port 18091 &
	@echo "fixtures on http://127.0.0.1:18090 and http://127.0.0.1:18091"

## NETWORK. Refreezes the public evidence snapshots. Respected robots.txt, a
## 2s per-host delay and no login/bot-wall workarounds. Run deliberately.
research-public-capture:
	$(PY) benchmark/datasets/research-core/public-evidence/build_public_evidence.py
