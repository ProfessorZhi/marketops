# MarketOps Lite Research MCP Tool Catalog — v0.4.0 CANDIDATE

This is the customer-facing stdio MCP. The tools are implemented in `scripts/research_mcp.py`; the entry every host config points at is the host-facing facade `scripts/research_mcp_facade.py`, which serves that same server with only the JSON Schema subset all supported hosts accept (see `references/CLIENT_INSTALL_STATE_2026-10-17.md`). It is registered by root plugin.json + mcp.json (Codex) or the WorkBuddy installer-generated connector mcp.json. It is NOT the stable/production MarketOps Business MCP. It deliberately has no submit-message, schedule, campaign, invoice, billing or login automation tools.

## Provider tool coverage

| Provider | MCP tool | Implementation | Actual level |
| --- | --- | --- | --- |
| **SerpApi official MCP (embedded)** | **marketops_serpapi_mcp_search(run_id,operation_id,params)** | MarketOps MCP Client → official https://mcp.serpapi.com/mcp `search(params,mode=compact)` | Multi-engine candidate results saved into MarketOps Run, real account billing/auth not verified |
| SerpApi REST (legacy) | marketops_search(provider="serpapi", mode=web/maps/images/news/videos/shopping/scholar/patents/autocomplete) | Explicit legacy HTTPS REST adapter | Bounded Google-category discovery; never both routes silently |
| Serper | marketops_search(provider="serper", mode=web/maps/images/news/videos/shopping/scholar/patents/autocomplete) | Same wrapper → official Serper HTTP API | Candidate source search, local receipt and event; no live verified API usage |
| Crawl4AI local | marketops_crawl(implementation="local") | Python crawl4ai AsyncWebCrawler installed in user's venv | HTML/Markdown retrieval material, not authoritative evidence; no client/browser smoke |
| Crawl4AI Cloud | marketops_crawl(implementation="cloud") | Official https://api.crawl4ai.com/scrape REST (Key) | Retrieved provider material; Cloud MCP remains separately optional |
| **TinyFish official MCP (embedded)** | **marketops_tinyfish(task=search|fetch,transport=mcp)** | MarketOps MCP Client → https://agent.tinyfish.ai/mcp `search`/`fetch_content` | Free/low-tier account use separately from paid Agent; requires provider auth and Run limits |
| TinyFish REST (legacy) | marketops_tinyfish(task=search|fetch,transport=rest) | Explicit compatibility REST | Separate API Key path, no silent retries/fallback |
| TinyFish paid Agent (embedded) | marketops_tinyfish_agent / marketops_tinyfish_agent_wait | `run_web_automation` and original `wait_for_run` over official MCP | Defaults denied; needs paid opt-in, server max_steps/max_duration, wallet quote, USD bound, approved public domain; submitted is NOT completed |
| Jev / TypeSafe | marketops_jev_choice(primitive=choice|noul|score) | Official typesafe-sdk TypeSafeClient.system_one/Choice/Noul/Score | Bounded typed judgment; may contain distributions; not evidence or business approval |

**The five vendors each have at least one implemented callable Lite MCP route in source**, but no complete vendor/cross-client E2E acceptance exists yet. This is not a claim all vendor functions or accounts are enabled.

**Capabilities NOT automatically enabled or tested on a customer's live account**: TinyFish raw browser CDP/Profile/Vault operations remain outside the managed allowlist; Crawl4AI official selfhosted/Cloud MCP is only reserved as an extension (Lite has local SDK + Cloud REST); unverified or high-cost vendor operations are gated. TinyFish Search/Fetch/Agent and SerpApi multi-engine MCP **are real in-process adapters**; they still require real credentials and user-authorized limits. No account/OAuth/billing is pre-authorized.

## Other tools: stage progress and memory

| Tool | Required input | What it really does |
| --- | --- | --- |
| marketops_setup_status | none | Read-only first-use state; returns CONFIG_REQUIRED with the two path questions, or the configured summary. Never creates a file |
| marketops_setup_configure | data_root, experience_file, optional confirm | Persists the two chosen absolute paths; refuses to invent a default or overwrite a foreign file; changing paths needs confirm=true |
| marketops_serpapi_mcp_search | run_id, operation_id, params (engine-specific dict) | One actual official SerpApi Streamable HTTP MCP search across engines, under current Lite provider reservation, saved candidate material |
| marketops_tinyfish_agent | run_id, operation_id, url, goal, max_steps, max_duration_seconds, max_charge_usd | Explicitly paid TinyFish official MCP submission, wallet verified on SAME session; never assume completion |
| marketops_tinyfish_agent_wait | run_id, operation_id, original_operation_id, agent_run_id, poll_index | Read an existing same-Run, SHA-verified asynchronous Agent run with official wait_for_run; never re-submit |
| marketops_capabilities | optional host_observed_tools, task_kind, tinyfish_wallet_snapshot | Read configured flags, installed SDK hints and reserved attempts; inspect selected API/SDK routes, official-host-MCP connection hints and unverified billing meters; no network or payments |
| marketops_start | run_id, scope, objective, rule_version, optional validated structured brief | Creates/resumes one durable Run, can stage the brief before any external attempt; no scheduler |
| marketops_propose | run_id, record_id, stage, kind, entity_key, facts, optional artifact_ids | Candidate venue/account/person/route, a structured research_brief observation (whose `minimum_targets` is optional — omit or `null` when the user asked for no quantity) or a review disposition in local SQLite. Cannot set verified, ready, approved or send_authorized |
| marketops_propose_batch | run_id, 1–20 items (record_id/stage/kind/entity_key/facts/artifact_ids) | Atomic, idempotent bounded writes; returns only record IDs/counts; no raw input echo |
| marketops_material_excerpt | run_id, up to3 artifact_ids, up to3 queries | SHA-checked local extracts, <=1200 total characters; untrusted and not fact verification |
| marketops_evidence_audit | run_id, optional sample limit | Per-Scene configurable fact-to-source excerpt audit; compact gap counts; no model, network or business authorization |
| marketops_status | run_id, optional limit (default 20), cursor, kind, entity_key | Reads persisted candidates through the workflow page contract: a page fixes a row high-water mark, later appends land in a new query, and a cursor belongs to its Run and filters. One status isn't full-market coverage |
| marketops_next | run_id, optional limit (default 10), previous_snapshot_id, after_attempt | Bounded structural next-step batch from the workflow core: recommended stage/skill, missing-fact list, `quantity_target_status` and `disposition_counts`. A `HOLD` target is an unresolved gap; a justified `EXCLUDED`/`DUPLICATE` is resolved but never counts toward an explicit positive minimum, and an all-excluded sample is not a successful positive delivery. It recommends work; it grants no spending authority and is not a fact or SOP verdict |
| marketops_events | run_id | Last 20 events with short execution-route details, latest Lite provider API/SDK/auth/meter receipt and blocker summary; external official MCP not observed |
| marketops_note | run_id, note_id, stage, category, problem, action_taken, observed_result, evidence_ref | Appends one de-identified bottleneck note and refreshes the collectable experience file. Rejects emails/URLs/keys/cookies; has no field for hidden reasoning |
| marketops_export | run_id, output_path | Legacy candidate-only UTF-8-BOM CSV utility; **not part of the main workflow contract** and not a confirmed XLSX |
| marketops_export_xlsx | run_id, delivery_id | The explicit workbook-only path: one immutable 9-sheet candidate workbook, per-sheet CSV counterparts and a SHA-256 MANIFEST.json |
| marketops_experience_draft | run_id, scope, rule_version, output_path | Writes unapproved experience.json from persisted incidents/events under approved experience directory |
| marketops_handoff | run_id, operation_id | **The primary final path.** Exactly one research snapshot — the workbook — then the unapproved experience draft and the configured summary refresh. A failed draft or summary refresh is PARTIAL/HOLD carrying partial_reasons, and the receipt still names the workbook that really exists. Immutable path conflicts stay errors |

All real network calls require an existing approved Run, client-set data root, explicit network on and two independent cumulative attempt limits:
- Search: MARKETOPS_SEARCH_ALLOW_NETWORK=1 and MARKETOPS_SEARCH_MAX_CALLS >0.
- Crawl4AI/TinyFish/Jev: MARKETOPS_RESEARCH_ALLOW_NETWORK=1 and MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS >0.

TinyFish's paid Agent has an additional step×wallet quote + duration + per-call USD guard, but the actual charge still requires provider reconciliation. The limit is an **attempt reservation cap**, not a token/currency budget; users must set provider-level limits too. Only a single explicitly selected provider per operation is invoked, and an unknown-result request is never retried under its old operation_id.

For known webpage acquisition, an operator-approved public HTTPS hostname must match MARKETOPS_FETCH_ALLOWED_DOMAINS. This is a client-side guard, not a full browser network sandbox or defense against all DNS rebinding; the operator must also use machine/network access policies and provider-side protections.

## Staging data and provenance

- state/research-lite.sqlite3 stores Runs, candidate objects and material references. It never becomes the MarketOps production canonical Market Graph, and this Lite server does not authorize formal contacts.
- state/research-events.sqlite3 stores operation events, not raw documents or credentials.
- artifacts/<run_id>/ keeps separate provider_extracted Markdown and (when local crawl supplies it) rendered_html DOM, plus their SHA. Rendered HTML is not original HTTP response bytes; an excerpt or source hash never becomes fact certification.
- search-receipts/<run_id>/ stores normalized search lead JSON.
- state/dual-search-ledger.sqlite3 and state/provider-reservations.sqlite3 reserve at-most-once external attempts in this local data root.
- exports/<run_id>/ candidate CSV, experience/<run_id>/ unapproved experience package.

**No silent workflow promotion**: User-facing business Skills may propose and organize account/venue/person/contact facts but cannot mark them ready or verified through this staging MCP. After E2E, formal route promotion should call the existing MarketOps domain rules with real identity/approval, not replicate A/B/C/D grading inside Prompt text.

## How to interpret status claims

- code_present: file and tool handler exist in GitHub.
- importable: Python SDK and dependencies actually installed.
- tool_listed: real client completed MCP handshake and shows catalog.
- auth_verified: signed into selected Provider with its own credential.
- live_called: single actual approved call returned a provider receipt.
- material_saved: fetched page/typed judgment saved and read back.
- business_verified: human/SOP checked facts, proper source and bindings.
- end_to_end: same Run traversed stages and delivered agreed Excel/CSV, plus experience with zero silent drop.

Today's candidate is at code_present for all five, not full auth/live E2E.


## Actual XLSX export (v0.4 candidate extension)

`marketops_export_xlsx(run_id, delivery_id)` writes an immutable `Research_Result.xlsx` with **9 candidate business/audit worksheets**, 9 matching CSV files and an actual-file SHA-256 `MANIFEST.json`. It reads the same Lite SQLite candidate store, verifies source material by rereading the real bytes, reopens ZIP/XML/CSV before returning, and refuses an empty Run, wrong-run references, corrupted source bytes and a reused delivery ID.

The manifest carries a `goal_assessment` computed from **the same record snapshot as the workbook**, and the 本轮说明 sheet restates that structural goal status, whether it is satisfied, the gap counts and the recommended next Skill. This is a structural completeness check only: it is **not** semantic fact verification, **not** SOP grading and **not** market coverage. When the structural goal is unmet the gaps are recorded in the existing journal as a bounded note, so a successful file export and an unmet business goal can coexist and both are reported. Repeating a delivery for an unchanged snapshot does not duplicate that note, and a later satisfied snapshot does not erase the earlier gap record.

All formal grades stay `RULES_UNKNOWN`; the Lite store cannot independently approve A/B/C/D or outreach. See [E2E_VERIFICATION](E2E_VERIFICATION.md) and [WORKFLOW_CONTRACT](WORKFLOW_CONTRACT.md).

Generic E2E quality requires Scene-defined claim anchors and a `evidence_assessment` in the manifest. Source-byte HASH_OK without a precise venue/property/relationship excerpt is technical integrity, never business acceptance. The CLI probe for Claude Code uses its `CLAUDE_PLUGIN_ROOT` variable in .mcp.json; other host manifests retain their own root syntax. Keep unpaid smoke tests tiny (fixtures first, then 1–3 public pages), no automated widening of provider scopes.

### Repeated attempts and Cloudflare/CAPTCHA

Existing local ledgers enforce at-most-once for an identical search or provider request in the same Run even when a different operation ID is supplied. After a known 403/429/challenge response, additional fetch/crawl calls to that public host refuse with `HOST_PREVIOUSLY_BLOCKED_SAME_RUN`. Only a SHA-256 of the host is retained in the provider reservation row; the journal records reason codes, not URLs. This does **not** establish a permanent block outside the Run, and does not claim that other pages lack evidence. Repeated restricted-provider requests are not automatic authorization to reroute around a challenge.

`marketops_next` is read-only and compares an optional caller-supplied prior snapshot after a real attempted batch. It returns a no-progress stop/switch hint without adding a scheduler or editing business status. A short deidentified `marketops_events` summary supports experience notes; a status code does not prove its root cause.

## Embedded official MCP and optional additional vendor services

The single MarketOps Research stdio MCP exposes **23 tools** (including 3 new official MCP bridge tools) and 17 Skills. `marketops_capabilities` reports the per-environment 5-provider matrix, bounded attempts and SDK/credential-presence *hints*; it never logs into a vendor or spends money. `marketops_jev_choice(primitive=choice|noul|score)` is one tool, not three.

**TinyFish Search/Fetch/Agent-wait and SerpApi multi-engine search are actually implemented via an embedded Python MCP Client**. A real call uses `initialize → tools/list → tools/call` against the official fixed HTTP MCP endpoint, under the same Run budget reservation, store and event journal as other MarketOps provider operations. The user does NOT need to install TinyFish/SerpApi as additional host MCP plugins. Vendor Key or optional TinyFish OAuth must still be supplied and verified. TinyFish paid Agent requires wallet, hard steps/duration and explicit per-request approved USD bound. TinyFish Browser CDP/Profile/Vault are not in the MarketOps safe tool allowlist. Crawl4AI local SDK is the default; its independent Docker/Cloud MCP remains optional. Serper official REST and TypeSafe official SDK do not need extra MCPs.

**Only a separate official vendor MCP directly connected to the host, bypassing MarketOps, falls outside MarketOps Lite's local reservation journal.** The reservation ledger limits attempted calls, not actual dollar charges; provider-wallet/invoice reconciliation and OAuth validity are separate requirements. Do not call unaudited paid features merely because a tool exists.

## What API/MCP and billing mean at runtime

The host calls **MarketOps via MCP stdio**, then MarketOps acts as **MCP Client to TinyFish/SerpApi official HTTPS Streamable HTTP MCP**, **REST client to Serper/optional Cloud and explicitly selected legacy adapters**, **local Crawl4AI Python SDK**, or **TypeSafe Jev Python SDK**. Using two layers of MCP does not imply two provider bills, but provider charges depend on each underlying real operation. See [Transport/auth/billing receipt](PROVIDER_TRANSPORT_BILLING.md).

Individual successful Lite provider receipts include `invocation` with supplier, entrypoint, real outbound transport, API Key presentation (not value), meter, local reservation status, and `actual_amount:null` with `charge_verified:false`. A blocked tool also reports the selected path when known. Ledger reservations are **not dollar costs or paid invoices**; the actual charge remains UNKNOWN until official billing receipt reconciliation. `marketops_events` summarizes only the latest 20 and never asserts visibility into direct official MCP calls.

## Embedded official MCP details

See [EMBEDDED_OFFICIAL_MCP](EMBEDDED_OFFICIAL_MCP.md) for actual MCP client handshake, OAuth Keyring, fixed endpoints, free vs paid TinyFish, SerpApi generic params and evidence/attempt settlement. **Only host-direct vendor MCP calls bypass the Lite ledger; calls made by the embedded client do not.** The unified MarketOps tool list now has **23** tools; this is not 23 separate external vendor MCP connections.
