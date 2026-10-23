# Plan 01: Bruce research plugin delivery

## Overview

Based on `docs/proposals/proposal-bruce-plugin-delivery.md`, integrate the existing
PR #8 research plugin in an isolated worktree. Source edits remain in the
worktree. Test output, review markers, temporary databases, installed packages,
and archives belong under the admitted external artifact root. Credentials stay
in the existing vault or the customer's OS credential store.

## Phase 1: Contract and baseline

Objectives: verify sources, capture current failures, and agree exact ownership.

1. Pin the public candidate and the private Bruce analysis revisions. Translate
   only sanitized methods into the public package, preserving provenance.
2. Read the installer, runtime composition, adapters, event/experience storage,
   workflow and delivery contracts. Record concrete default-path defects.
3. Review proposal and plan twice against those modules. Store review findings
   and pending/done markers outside the source tree per machine policy.

Acceptance: reviewed proposal/plan without unresolved critical/warning findings;
baseline SHA, credential-presence inventory and runtime metadata retained.

## Phase 2: Implementation (independent work within one phase)

Each workstream starts with a failing behavior test and owns its files. Changes
to the common `research_mcp.py` are integrated by the root agent from explicit
helper APIs, avoiding concurrent edits.

### A. Real provider composition and first-use admission

- Owner: MCP integration workstream. Own `research_wiring.py`, the provider
  adapter modules, official MCP transport, and new runtime/bootstrap helpers.
- Add failing tests for default SerpApi session binding, credential injection,
  Crawl4AI egress binding, consent-aware policy, result payload preservation,
  and refusal before any unapproved dispatch. Reuse existing adapter contracts.
- Implement an actual read-only schema/bootstrap path and bounded runtime
  configuration; do not satisfy the gate with synthetic PASS evidence.
- Root integrates MCP handlers and any common launch wiring.
- A owns `research_runtime_setup.py`: actual build/QA verification, explicit
  finite local consent, tools/list-only bootstrap and provider-specific
  readiness. Compare captured schemas with reviewed contracts before pinning;
  unavailable nonselected providers do not block the selected provider.
- A owns one-loop-per-operation SerpApi sessions and lazy persisted engine
  discovery after admission, real restricted Crawl4AI egress, consent-aware
  Policy, finite caps and canonical identity in the existing Ledger. Preserve
  spent reservations and UNKNOWN exposure. Old tool names use the same path.
- Initial T1/T2 REQUEST_COUNT mode requires explicit local provider/task/domain/
  count consent; unknown account balance and dollars remain unknown. Never claim
  a wallet floor without sourced B0. Test restart/no-refill, no MCP consent grant,
  cross-entry duplicate refusal, and unaffected ready providers. T3 remains off.
- Persist sources/artifacts/events on successful calls, so MCP-returned IDs can
  feed Jev and the workbook without a test script filling missing data itself.
- Acceptance: default assembled adapters use real transport implementations;
  absent keys/consent/refused schema cause named refusal and no dispatch; a
  fixture and then a bounded real call yield usable persisted result references.

### B. Installation, account setup and delivery package

- Owner: installation workstream. Own `setup_clients.py`,
  `build_client_bundles.py`, onboarding/catalog, install wrappers and customer
  setup documentation. Coordinate launch helper edits with A/root.
- Add failing tests for paths with spaces, idempotent retry/resume, packaged
  internal MCP clients, matching Python/platform dependencies and secret-safe
  receipts. Installation must preserve existing customer data/configuration.
- Provide a Windows entry point and POSIX equivalent, supported Python setup,
  guided local API-key storage and explicit finite research configuration.
- Fix the supported line to Python 3.12 Windows/Linux x64 with hashed locks and
  `pip check`; write ownership/source/stage state before copying and resume only
  same-source owned partial installs. Preserve research data and configuration.
- B owns `research_credentials.configure_keys/load_into_environment`: native
  keyring, hidden input, env precedence, unset placeholders, no plaintext fallback.
  Root wires the loader before server construction. B calls A's runtime setup
  helper for consent/bootstrap rather than implementing another policy plane.
- Add portable source archive mode and supported Codex marketplace add/plugin add
  (verified CLI behavior; older clients use the exact desktop install steps);
  report WorkBuddy `PENDING_HOST_IMPORT` with exact target-machine instructions.
- Verify current official account/key/trial-credit links for Serper, SerpApi,
  TinyFish and TypeSafe. Local Crawl4AI needs no fifth account.
- Acceptance: source ZIP can install into a clean location, list one MCP and
  included Skills, report dependency/configuration status, and generate working
  host configuration. Windows tests/CI cannot be labelled real WorkBuddy use.

### C. Bruce methods and cross-run experience feedback

- Owner: experience workstream. Own new `research_experience_recall.py`, seed
  data, related tests, retrospective/main research Skill updates. Root owns
  integration into `research_mcp.py` and `research_workflow.py`.
- Write failing tests for Run A note -> relevant recall in Run B -> evidence-
  linked feedback -> Run C sees outcome/counterexample; cross-scope/rule
  mismatches, missing evidence, duplicates and immutable records.
- Implement small additive storage and bounded ranking, not a new backend.
- Join Run scope/rule and validated provider metadata, resolve event/material
  evidence with ownership/path/hash checks, distinguish use from reported
  effectiveness, preserve counterexamples and count independent target Runs.
- Seed source: private analysis main `627ff430c64ebe5339ae15034343db4ff726fed6`.
  Skills are `skills/marketops-research/SKILL.md` and
  `skills/marketops-research-retrospective/SKILL.md` under the plugin.
- Acceptance: recall works after process restart, rejects unsupported evidence,
  records actual use, preserves contradictory outcomes, and cannot override
  SOP, permissions or costs. Sanitized seeds trace to a fixed analysis revision.

### D. Bounded research fallback

- Owner: fallback workstream. Own new `scripts/research_fallback.py`,
  `tests/test_reading_fallback_chain.py` and the provider-router Skill. Root integrates
  MCP `marketops_research_step` and injects existing managed-call/admission
  functions; no runtime/policy/ledger core ownership moves.
- Review this added proposal and plan twice before implementing. Retain markers
  and findings under the external `bruce-delivery-20261020/d` artifact directory.
- TDD: discovery order and configured-provider skipping; Crawl4AI missing SDK,
  explicit ordinary failure or empty text switching to admitted TinyFish;
  denial/challenge/403/UNKNOWN/timeout/budget stopping; failed Jev returns manual
  review HOLD. Explicit locale and max_attempts 1..3 are validated before calls.
- Persist chain ownership and attempt intent in the existing journal database.
  Test completed replay, conflicting input, in-progress/interrupted HOLD,
  concurrent same-chain callers, deterministic child IDs, parent references,
  unchanged unknown-cost receipts and no retry of a provider or fee reset.
- Validate actual saved material references/hash and known payload shapes before
  counting success. Store compact receipts and append a concise final method
  observation; do not copy source content into the metadata journal.
- Acceptance: tests demonstrate a successful second provider after a permitted
  first-provider failure, exact stop cases with zero second dispatch, at most
  three attempts over repeat calls, and actionable candidate/HOLD receipts.
  An installed-MCP live task verifies the same root callback path separately.

Phase acceptance: targeted tests pass across A/B/C/D and full offline plugin suite
passes after integration. No change to production services or shared toolchains.

## Phase 3: Installed-package and real research validation

Dependencies: Phase 2 tests passing.

1. Root fixes the real-source fixture to supply exact source quotations, tests
   source drift/unsupported contact hold, and keeps evidence checks strict.
2. Build from the real production packaging path. Extract to a clean external
   directory and validate manifests, scripts, Skills, dependency completeness,
   archive hygiene, stdio initialize/tools/list and safe calls.
3. Load the existing four credentials only into an isolated test subprocess.
   Verify read-only MCP/auth/schema state first. Use a small bounded public task,
   finite provider request counts, no topups and no paid browser-agent task.
   Final acceptance must be an actual Claude Code or Codex model session loading
   the installed plugin and issuing its MCP calls. A Python harness is diagnostic
   evidence only. Restrict the agent to MarketOps plus necessary local reads,
   retain its tool-use/result trace, and cap model turns, wall time and estimated
   model spending independently of provider quotas. Iterate on observed failures.
4. Save/reopen raw sources, claims, candidates, usage/events and workbook. Verify
   actual useful records and every missing/excluded/HOLD object. Repeat/resume
   against retained inputs and measure avoided operations and experience reuse.
   Report unique entity/route units, source-supported facts, HOLD, dispatches,
   unknown costs and timing/intervention windows. Test new-task recall separately.
5. Run meaningful tests/lint/preflight and relevant CI. Report known root-suite
   baseline failures separately from plugin regressions, never suppress new ones.

Acceptance: a reproducible receipt with source SHA, provider/auth verdicts,
actual output/readback metrics, archive hash, and exact unverified host limits.
No unmeasured claim of improvement over Bruce's historical timing.

## Phase 4: Reviewable handoff

Dependencies: Phase 3 results recorded honestly.

Finalize source changes, Chinese installation/startup/credit guide, archive and
release notes. Create a reviewable PR against the appropriate public candidate
base, attach it to the chat, and provide local artifact links. Do not send Bruce
messages or publish to a plugin directory as part of this source-package task.
