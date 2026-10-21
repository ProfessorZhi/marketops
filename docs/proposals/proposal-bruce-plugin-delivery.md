# Bruce research plugin delivery

## Problem Statement

The public research plugin at `fa9bc90fcdd4c4ca8b8f8d1379971d30acffe2cb`
contains the research workflow, five provider adapters, embedded TinyFish and
SerpApi MCP clients, evidence storage, workbooks, and experience summaries.
Its default installation and runtime do not yet form a usable customer workflow.
The managed gateway constructs unbound adapters, defaults its policy to network
off even after deployment admission, and drops successful adapter payloads at
the MCP boundary. The installer lacks a Windows entry point and its Linux-only
dependency lock is unsuitable for the advertised cross-platform support.
Experience is accumulated, but task-specific recall and measured feedback are
not connected to subsequent research.

## Goals

- A source package Bruce can install through one visible entry point, with
  credentials entered locally and four official account/key/credit guides.
- One MarketOps MCP with TinyFish and SerpApi clients included in the package;
  Serper REST, local Crawl4AI, and TypeSafe Jev are integrated behind the same
  bounded research and persistence contracts.
- Resume research from a customer-owned data root; retain fetched materials,
  claim quotations, entity relationships, event/usage receipts and exports.
- Reuse Bruce's evidence-backed working methods and provider experience;
  retrieve relevant prior experience, record use/results/counterexamples, and
  make those observations available to later tasks.
- Demonstrate real useful research output, export readback, and measurable
  reuse. Report unknown business baselines as unknown, never invent a time
  saving relative to Bruce's historical work.

## Non-Goals

- No email sending, account creation, purchasing credits, or automatic changes
  to approved business policy. The customer registers four accounts.
- No new agent platform, remote memory service, or shared network SQLite file.
- No unsupported claim that Linux verification proves Windows client behavior.
- No copying private session transcripts, customer identities or secrets into
  this public repository or distribution.

## Design

### Installed runtime and embedded connections

Keep `scripts/setup_clients.py`, `build_client_bundles.py`, the existing
manifests, and `launch_research_mcp.py` as the installation/runtime owners.
Support Python 3.12 on Windows x64 and Linux x64 with platform-appropriate hashed
locks and `pip check`; reject unsupported Python minors before installation.
Add platform entry scripts and an interactive first-use helper (new files)
which select the supported Python runtime, destination and customer data paths,
prepare dependencies, show the official four-account guide, and store API keys
through the OS credential facility. Headless tests preload vault values into an
isolated subprocess environment; no automatic dotenv reader or developer vault
path is persisted. Values must never enter manifests,
receipts, process arguments, reports, archives or chat.

Installation owns new `research_credentials.configure_keys()` (hidden terminal
input/native keyring) and `load_into_environment(environ=None)` (four allowlisted
keys, explicit nonempty environment wins, placeholders count as unset). No safe
keyring means no plaintext fallback. Root invokes the loader before building
the MCP server. Installation ownership/source-hash/stage state is written before
copying; only an owned same-source partial install may resume, preserving data
and configuration. Codex receives the supported add/install/enable flow;
On the verified CLI, installation uses `codex plugin marketplace add` then
`codex plugin add`; there is no invented `install` or `enable` subcommand.
WorkBuddy receives a target-machine connector and `PENDING_HOST_IMPORT` until
its actual import is observed. A portable source ZIP is a separate product from
that machine-bound connector.

Fix `research_wiring` to construct real provider adapters lazily with the
credential, MCP session, and restricted egress implementations those adapters
already define. Keep default network refusal without explicit runtime consent
and finite limits; after valid admission the policy must reflect that consent.
Connect the existing `research_s0_preflight_gate.bootstrap_probe_schema` to a
new `scripts/research_runtime_setup.py`, owned by runtime integration. Its states
are unconfigured -> local dependencies/credentials present -> finite local
operator consent -> actual schema/account observations -> provider-specific
ready or named refusal. Release code revision, file digests and QA receipt come
from the actual tested build; installation facts do not impersonate independent
QA. A development package without QA stays diagnostic until the real test run
produces that receipt. Customers never edit S0 JSON. Probe actual schemas; do not manufacture
QA, authentication, pricing, or discovery evidence to satisfy the gate.

Capture raw tools/list in the existing read-only bootstrap and compare it with
the shipped reviewed capability contract before pinning a digest. Same-capture
hash equality alone is not schema review. Runtime checks global deployment
conditions plus the selected provider/capability; a missing other account does
not block it. Only the two MCP providers require MCP schema captures; local
Crawl4AI requires SDK/browser/restricted-egress readiness. Account observations
retain their real source/trust level; unknown credits or price remain unknown.
The local setup helper owns explicit finite request caps and allowed domains;
MCP callers cannot self-sign runtime consent.
Return usable, bounded provider results and stored evidence references through
`research_mcp.marketops_managed_call`, not only execution status. Legacy provider
tool names become compatibility wrappers over the same reviewed adapters and
ledger. Preserve their result shapes and artifact/candidate/event persistence;
there is no second TinyFish auth/schema path or implicit paid fallback.

Configure finite project/run/provider-account/operation caps in the existing
ledger without resetting spent reservations on resume. Preserve canonical
operation identity across old and managed names. Unverified credit baselines
and unknown monetary exposure remain explicit; request counts are not dollar
guarantees. Paid browser-agent tasks remain disabled in initial live tests.
The initial product mode is explicit local `REQUEST_COUNT` consent for selected
T1/T2 providers, task and domains. Unknown balance or USD cost does not become a
fabricated B0: this mode guarantees cumulative request limits only, not a 20%
wallet floor or a dollar ceiling. A sourced account baseline can additionally
enable the existing campaign-floor checks. MCP calls cannot create/expand this
consent, and starting/resuming a Run cannot replenish its project allowance.
SerpApi manages a real session within one event loop per operation and lazily
persists the engine index plus the selected engine schema under the data root.
These resource reads happen after admission with audit/accounting, outside the
tools/list-only bootstrap. Reuse the existing Crawl4AI restricted-egress binding
from `research_crawl4ai_live_local.SessionEgressBinding/binding_for` as production
code, then make the harness use that shared implementation.

### Experience reuse

Add `scripts/research_experience_recall.py` with a small additive store in the
existing event database. Historical Bruce methods are shipped as sanitized,
versioned advisory seeds with source revision and applicability, not customer
facts or presumed business rules. Select bounded prior-run notes by scope,
rule version, stage and provider. Expose recall and append-only feedback through
MCP; include relevant results when a Run starts/resumes and when its next batch
is selected. Feedback must bind a real target Run, experience reference and
stored receipt/evidence. Scope and rule version are joined from persisted Run
rows; provider comes from resolved event/material metadata, not free text.
Exclude the current Run and later observations when reconstructing an earlier
decision. Define resolvable event/material/note references: check Run ownership,
contained artifact paths and SHA readback. An event proves an operation, not a
business fact. Separate `used` from `helped/not_helped/contradicted/unknown`,
deduplicate independent support by target Run, and preserve negative evidence.
No feedback grants a business grade, network permission or budget.

Expose a bounded read-only Run list through the existing Lite store so a new
session can discover the Run ID, stable scope and rule version before resuming
or starting a related batch. Capability status must read the same local runtime
consent/admission and usage ledger that actual dispatch uses; legacy environment
switches cannot falsely label a configured runtime disabled. Explicit local
consent amendments retain their history, existing data and consumed/unknown
ledger usage in the same data root rather than forcing a new research store.

Keep existing `research_experience_summary.py` and handoff receipts as readable
views. Add measurements for useful results, evidence gaps, actual operations,
reuse and interventions with explicit units. The existing `Research_Experience.md`
remains a derived collectable view; the configured data root is authoritative.
Seed provenance is private analysis main
`627ff430c64ebe5339ae15034343db4ff726fed6` with relevant GP/INT identifiers.
Publish generalized advisory methods only, not private excerpts or unsupported
historical timings/ROI thresholds.

### Research and acceptance

#### Bounded fallback for a research step

The explicitly requested fallback is introduced as
`scripts/research_fallback.py::research_step` (new), above the existing
`research_mcp.managed_call` rather than inside a provider or a second ledger.
The host exposes `marketops_research_step`; root supplies the managed callback
and local `research_runtime_setup.admission` callback. Callers cannot supply
their own readiness or permission. Existing task names are preserved:
`source_discovery` selects Serper, SerpApi, then TinyFish search;
`known_public_url` and `batch_public_pages` handle one current page through
Crawl4AI then TinyFish Fetch; `semantic_choice` tries evidence-bound Jev Choice
and otherwise returns source/manual review HOLD. No cloud/browser/Agent or T3
capability belongs to these chains. Discovery requires explicit task country
and language instead of inventing a US locale.

Each chain permits at most three managed invocations, each with a deterministic
child operation ID and parent reference. Readiness is rechecked before every
candidate; missing/unselected provider capabilities may be skipped. Confirmed
ordinary technical failure or empty usable material may advance to the next
admitted provider. Explicit source denial, HTTP 401/403/429, challenge/WAF,
domain/egress refusal, exhausted shared budget or invalid input stops the chain.
An exhausted provider-specific allowance may skip that provider, but project,
Run and operation ceilings are shared stop conditions and are never reset.
Ambiguous timeout, UNKNOWN outcome, unclassified exception or interrupted
dispatch also stops; a different operation ID must not erase that exposure.
An explicit terminal failure with an unknown dollar amount may use another
already-admitted provider, while preserving unknown cost and existing ledger
reservations. A successful response needs saved nonempty material or real search
hits, not merely `status=OK`; semantic judgments remain candidates.

Add chain/attempt receipt tables to the existing event database, not another
usage ledger. Atomically claim a chain before callback invocation, persist each
attempt's intent before dispatch, and replay completed receipts without network.
An identical running/interrupted chain returns HOLD, never a fresh dispatch;
conflicting parameters for the same chain ID are refused. Receipts retain
provider/capability, child operation, parent reference, actual outcome, source
IDs and unknown charges; the root's ledger remains the source of spend/dedup
truth. Successful materials stay in the existing store; receipt replay returns
references rather than duplicating raw sources. A concise final event/method
note feeds the existing experience flow. These chains do not establish that
research facts are true or that a human baseline has improved.

Fix the real-source test's missing `claims[{field, artifact_id, quote}]` rather
than weakening `research_delivery`'s literal evidence check. Reuse the existing
store, workflow and XLSX implementation. Exercise a small public research task
through the installed stdio MCP, read saved sources and exported workbooks back,
then resume/repeat without redundant retrieval. Independently check entity,
role and contact associations against saved text. Show incomplete results as
HOLD, not as zero or approved contacts. Measure unique organizations/routes,
source-supported facts, HOLD counts, actual dispatches, unknown charges, timing
window and observed interventions with explicit denominators. Retained-input
replay proves reuse only; separately test applicability to a new task.
Installed acceptance retains the real wiring, Registry, Policy, Ledger and
store, replacing only the lowest HTTP/MCP transport in offline tests.

### Release and documentation

Produce a complete source archive, hashes, a concise Chinese START_HERE guide,
official registration/key/trial-credit instructions, startup prompts, and a
verification receipt. Reconcile stale customer-facing instructions with the
implemented entry points. Keep historical audit reports intact.
Credit guidance carries official URLs and check date 2026-10-20. TypeSafe
amount stays unknown pending console confirmation; existing TinyFish accounts
check balance rather than claim signup credit again. No invented expiry dates.

## Alternatives

- Separate vendor plugins in each host: rejected because calls and data would
  escape the unified task/receipt/experience workflow requested by the user.
- Use only the legacy direct provider wrappers: rejected as the long-term
  default because they bypass the reviewed adapter integration and drift in
  schema/authentication handling.
- A new vector-memory service: unnecessary for bounded, scoped operational
  experience in the existing local event store.

## Open Questions

- Historical Bruce timing/cost claims are not an adequate benchmark. Use a
  measured current task and retained-input replay; a future Bruce pilot supplies
  a human baseline without blocking implementation.
- Official trial offers and account permissions can change. Documentation must
  carry checked date and official links; runtime checks report actual status.
- A real Windows/WorkBuddy device may be unavailable here. Add Windows CI and
  executable installer tests and state remaining host-level verification clearly.
