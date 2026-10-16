# Release gate — do NOT distribute ZIP to Member-A before local E2E

Status of this branch: **code candidate**, not a verified product release. This document is a test contract, not a PASS report. It draws negative tests from the 2026-09-26 Member-A session audit; historical thresholds are not the same as today's approved SOP.

## A. Source and dependency verification (offline)

- [ ] 从插件根目录执行 `python scripts/preflight.py`，确保 status=STATIC_PASS_NOT_RUNTIME_VERIFIED 且errors为空。若缺mcp/SDK仅可视为警告，不能视为真实MCP可用。


- [ ] Identify pinned git commit/branch. All plugin files are under plugins/research-tools; no personal email/Key/session transcript/hard-coded server endpoints.
- [ ] Compile every *.py with the selected Python >=3.11; test Python 3.11 and 3.12 if target supported.
- [ ] Validate portable root plugin.json/mcp.json schema and compatibility fallback .codex-plugin/plugin.json + .mcp.json. The two MCP catalogs must expose the same **single** marketops-research service.
- [ ] 16 Skills have unique YAML frontmatter and relative links resolve. Skill text never silently overrides country profile, official SOP or approved human decisions.
- [ ] WorkBuddy generated connector has exactly one stdio server, auth_mode=token, keys in token-schema only, source kebab-case, client version >=4.24.0; Python and script path belong to the **test machine**, not the developer's machine.
- [ ] Full SDK imports pass: mcp>=2.2,<3; typesafe-sdk>=0.7. Optional local Crawl4AI install explicitly staged and browser doctor run.
- [ ] Run all offline unit tests below with zero network/paid credentials.

Command from plugin directory:

~~~bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
python scripts/setup_clients.py --destination /ABSOLUTE/EMPTY/TEST-DIR --client both
~~~

The setup command without --apply must have **zero filesystem effects**.

## B. All five providers: synthetic receipts + fail-closed (offline)

- [ ] SerpApi and Serper web/maps mock responses normalize without swapping API keys; distinct provider names retained; snippets stay discovered, never verified. Repeated operation ID and higher call ceilings behave as declared.
- [ ] Crawl4AI Cloud mock POST and browser-local import-missing behavior. URL/approved domain policy rejects private/local/unapproved hosts BEFORE reservations. Derived Markdown hash cannot be advertised as raw page hash.
- [ ] TinyFish Search/Fetch mocked official REST endpoints; fake Key never reaches artifact or agent preview; Cloud Agent remains **outside this gateway**, not mistakenly called by skill-only.
- [ ] Jev Choice official SDK's semantics: candidate option must be in declared labels; if SDK/Key unavailable the call is unavailable, not auto-passed; no A/B/C/D client promotion based on model answer.
- [ ] All providers blocked by default network switches and cumulative independent attempt caps; unsupported / extra credential placeholder fails closed. Changing env variables inside a hosted Agent chat is NOT operator approval.
- [ ] External attempt consumed/failed/unknown status remains visible, and caller cannot get a false “verified” result. If journal save fails, user gets named unsaved result.

## C. Complete Lite research *staging* E2E (offline fixtures)

- [ ] Start one Run with original objective and country/approved business SOP revision; verify re-start idempotence and collision refusal.
- [ ] SerpApi mock discovers 3 leads; staging SQLite contains 3 **source candidates** with stable IDs; zero main-store official contacts.
- [ ] Crawl4AI mock returns usable source material; artifact saved and re-read with SHA, relationship only proposed after reading evidence; challenge page/empty content cannot be used as verification.
- [ ] TinyFish mock fetch supplies additional public source data; verify source/operational context cannot be accidentally cross-bound.
- [ ] Jev mock judgment creates a decision artifact, but can't itself create verified company, literal email or formal ContactRoute.
- [ ] Agent proposes venue/account/person/contact candidate facts; read same data in a second simulated agent session without copying CSV. A second worker does not double-create record.
- [ ] Reports give actual real row counts and unresolved items, never missing rows as zeros. Export candidate-only CSV within customer-approved exports root, with reference links; exercise Excel workbook Skill separately in a client with actual .xlsx tooling.
- [ ] Generate experience candidate from real operation receipts; root cause unconfirmed by default, source IDs attached, no memory of hidden chain-of-thought, no automatic SOP change.
- [ ] No outgoing email/form submission, backend production marketops.db mutation or general account provisioning occurs.

**This stage validates only portable staging, not the full MarketOps formal contact grading or market coverage.** The missing production/business migration is a distinct blocker that must be resolved before claiming the whole business chain is finished.

## D. Local provider / client E2E (human-approved real calls)

- [ ] Member-A or operator has provided *credentials in own OS/WorkBuddy only*. Confirm which services are purchased/free and per-account settings.
- [ ] Search: one real SerpApi and one Serper equivalent bounded request, compare outcome/units and registered source receipts.
- [ ] Crawl4AI: choose client-side local or Cloud. Local needs crawl4ai-setup/doctors and public-domain rules; Cloud needs only Cloud key and explicit data-processing approval. A successful source fetch must produce real source artifact and readback.
- [ ] TinyFish: Search, Fetch, and (as **separate official connector**) only an explicitly approved read-only browser Agent task if that capability is a requirement.
- [ ] Jev: one approved Choice request and separate subsequent evaluation of true labels and rate. Do not claim Noul/Score tools exist in v0.4.
- [ ] WorkBuddy connector installed/imported from a stable folder on the customer test machine. Tool listing and calls verified in client, not merely running Python directly.
- [ ] Codex local plugin installed through current plugin marketplace and Skills discoverable. Its one MCP starts with the dedicated installed Python venv. Tool listing/permissions and a no-network dry-run proven.
- [ ] TinyFish and Crawl4AI third-party MCP (if enabled separately) have distinct authentication and cannot bypass MarketOps data capture under the pretense that calling a direct MCP creates an internal event.
- [ ] User can cancel/stop approved work. No secret in tools/list, error body, SHA manifests, support bundle or generated README.

## E. WorkBuddy / Codex cross-session handoff + Member-A review

- [ ] Use a Member-A-like task with real approved country/Scene/roles. Real 3–5 venues/organizations followed end-to-end: source → verified material → candidate entity → relevant roles → public contact signal → human verified formal route (once real domain-store integration is ready).
- [ ] Demonstrate no dropped rows: acquired/extracted/staged/rejected/held counts reconcile. New source discoveries vs actual verified entities and market coverage never conflated.
- [ ] Job or tool success alone cannot pass: check actual on-disk artifact and read it back, check literal email in source and role source age.
- [ ] Both clients can read same Run through one local MCP service where host and file permissions permit, and no concurrent CSV modifications.
- [ ] Run the 12 Member-A Skill replay cases with separate development and holdout fixtures and audit each failure reason.
- [ ] Excel output generated with actual spreadsheet engine, content inspected for row count, source URLs, formula injection and no silently dropped HOLD records. CSV fallback must be called CSV, not claimed XLSX.
- [ ] Experience candidate auto-populated with real receipts; human validates explanation, counterexamples and versioned change. Only after independent approval may a new Skills release be published.

## F. Only after PASS: prepare distribution ZIP

- [ ] Record test machine OS, Python, Codex/WorkBuddy versions, pinned Git SHA, CLI/test results, actual Provider query IDs (sanitized), source hash, version, date, reviewer.
- [ ] Build **source-only archive** without customer data/venv/logs/Keys. Include MEMBER_A_INSTALL, installer and pinned dependency requirements. Users rebuild machine-bound WorkBuddy connector **on their own machine**, not by copying a dev's generated connector directory.
- [ ] Validate extracted archive on a clean Windows pilot machine; a fresh Agent must follow setup steps, generate the local marketplace/connector, and accurately distinguish only-installed from authenticated.
- [ ] Compute published archive sha256 and produce rollback notes before sending to Member-A.

**Current state: these boxes are NOT checked merely because test functions have been committed.** GitHub CI failures or missing logs must be investigated, not converted to PASS. Explicitly refuse to publish a “ready-to-use ZIP” until D/E succeed.


## E. New candidate XLSX and real public-source E2E

- [ ] `python -m unittest discover -s tests -p test_research_delivery.py -v` passed, including good source, wrong SHA, unsupported email and immutable duplicate ID.
- [ ] Opt-in `MARKETOPS_LIVE_PUBLIC_E2E=1 python -m unittest discover -s tests -p test_research_live_e2e.py -v` read City of Lincoln Council actual page, saved its bytes, wrote a real XLSX, independently reopened the workbook and checked public email evidence. Record runner URL, SHA, time. This is *real-source candidate* E2E, NOT a paid-provider or approved-business E2E.
- [ ] On real Codex and WorkBuddy, confirm `tools/list`, `tools/call marketops_export_xlsx`, file readback, customer data isolation, Run and provider receipts.
- [ ] Connect approved MarketOps business A/B/C/D domain and authenticated reviewer before claiming formal verified contacts, or preparing a Member-A customer release.

See [E2E_VERIFICATION](E2E_VERIFICATION.md) for the L0–L5 ladder.
