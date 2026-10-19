# Wave-1 independent quality gate (`qa_gate/`)

Independent **test/security** review of the MarketOps Capability Gateway Wave-1.
This package adds tests only. It is not a feature branch: no file under
`scripts/` is modified, and `research_mcp.py` /
`research_provider_extensions.py` (Integrator-owned shared files) are read-only
inputs here.

* Base revision: `b2bcdcd24862a923f7b32eff75bfcc2756ae767b` (PR #73 head).
* Verdict for the wave: see `docs/wave1-qa/INDEPENDENT-QA-MATRIX-20261017.md`.

## Run it

```bash
./run_gate.sh                       # writes JUnit XML + a run-context file
INTERPRETER=/path/to/python ./run_gate.sh /tmp/gate-out
```

Or directly:

```bash
cd plugins/research-tools/marketops-research-assistant/tests
/home/operator/artifacts/marketops-capgateway-independent-qa-20261017/venv/bin/python \
  -m pytest qa_gate -q --junitxml=/tmp/qa-gate.xml
```

### Why these two flags are not optional

* **Name the interpreter.** The `pytest` on this host's `PATH` is not a working
  install; a bare `pytest` reports an import error that reads like a red test.
  Acceptance checklist §G-5.
* **Use `--junitxml`.** pytest's final count line does not survive a redirect on
  this host, so a `tail` of a log file silently shows the wrong total.
  Acceptance checklist §G-6.

## Reading the result

| State | Meaning here |
|---|---|
| **pass** | the contract is met by this revision |
| **fail** | the contract is **not** met — the failure message names the invariant and what would make it pass. These are the deliverables, not breakage. |
| **skip with `BLOCKED:`** | the test cannot execute — a missing optional dependency or a missing external authorization. **Never a pass.** §G-1 requires the skip to name the specific missing authorization. |

A red test that encodes an unmet contract is intentionally red. Do not "fix" one
by loosening its assertion; when the implementation lands, the test goes green
with no edit.

## Layout

| File | Covers |
|---|---|
| `test_qa_ledger_concurrency.py` | §P1-03 ledger: concurrency, dedup, ceilings, crash states |
| `test_qa_t3t4_authorization.py` | §P1-04 tiers: what a shell-capable host Agent can authorize |
| `test_qa_browser_ssrf.py` | §P1-01 browser/HTTP egress and the adversarial SSRF matrix |
| `test_qa_mcp_hostile_schema.py` | hostile upstream `tools/list`, prompt injection, schema drift |
| `test_qa_provider_parsing.py` | hostile provider responses; credential non-leakage |
| `test_qa_dedup_provenance.py` | source provenance, candidate→core boundary, transport dedup |
| `test_qa_packaging_clients.py` | §P1-02 distribution surface, three clients, Windows harness |
| `test_qa_contract_invariants.py` | `INTERFACE-CONTRACT.md` §10 (IR/IL/IA/IF), one test per invariant |

## Offline guarantee

No test opens a socket, reads a credential, or reaches a provider. Name
resolution is replaced by a fake resolver, HTTP by injected openers, connections
by `SocketSentinel`, and provider payloads by synthetic literals. The only
"secrets" in the tree are the string `qa-gate-synthetic-not-a-credential` and
`qa-gate-SYNTHETICKEY-DEADBEEF`, used to prove a *real* secret would not leak.
