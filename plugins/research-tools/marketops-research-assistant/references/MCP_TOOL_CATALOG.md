# MarketOps Lite Research MCP Tool Catalog — v0.4.0 CANDIDATE

This is the customer-facing stdio MCP in scripts/research_mcp.py, registered by root plugin.json + mcp.json (Codex) or the WorkBuddy installer-generated connector mcp.json. It is NOT the stable/production MarketOps Business MCP. It deliberately has no submit-message, schedule, campaign, invoice, billing or login automation tools.

## Provider tool coverage

| Provider | MCP tool | Implementation | Actual level |
| --- | --- | --- | --- |
| SerpApi | marketops_search(provider="serpapi", mode=web/maps) | MarketOps dual_search_mcp.py → official HTTPS REST API | Candidate source search, local receipt and event; no live verified API usage |
| Serper | marketops_search(provider="serper", mode=web/maps) | Same wrapper → official Serper HTTP API | Candidate source search, local receipt and event; no live verified API usage |
| Crawl4AI local | marketops_crawl(implementation="local") | Python crawl4ai AsyncWebCrawler installed in user's venv | HTML/Markdown retrieval material, not authoritative evidence; no client/browser smoke |
| Crawl4AI Cloud | marketops_crawl(implementation="cloud") | Official https://api.crawl4ai.com/scrape REST (Key) | Retrieved provider material; Cloud MCP remains separately optional |
| TinyFish | marketops_tinyfish(task="search" or "fetch") | Official api.search.tinyfish.ai or api.fetch.tinyfish.ai via X-API-Key | Search/Fetch, not interactive Agent; Agent and browser tasks use separately authorized official MCP/CLI |
| Jev / TypeSafe | marketops_jev_choice | Official typesafe-sdk TypeSafeClient.system_one/Choice | Typed semantic candidate choice; Noul/Score skill design exists, but not registered here |

**The five vendors each have at least one implemented callable Lite MCP route in source**, but no complete vendor/cross-client E2E acceptance exists yet. This is not a claim all vendor functions or accounts are enabled.

**Capabilities that are NOT wired into this MCP**, so no Skill may present them as available here: Jev/TypeSafe Noul and Score; the TinyFish interactive browser Agent (only Search/Fetch are registered — Agent/browser tasks need the separately authorized official MCP/CLI); the official Crawl4AI Cloud MCP as a connector (only the REST route is wrapped); and any SerpApi/Serper capability beyond the implemented search modes. No vendor route has a live billed call, an account login or a credential recorded by this repository.

## Other tools: stage progress and memory

| Tool | Required input | What it really does |
| --- | --- | --- |
| marketops_setup_status | none | Read-only first-use state; returns CONFIG_REQUIRED with the two path questions, or the configured summary. Never creates a file |
| marketops_setup_configure | data_root, experience_file, optional confirm | Persists the two chosen absolute paths; refuses to invent a default or overwrite a foreign file; changing paths needs confirm=true |
| marketops_capabilities | none | Read configured flags, installed SDK hints and current limits; no network |
| marketops_start | run_id, scope, objective, rule_version | Creates/resumes one durable local Run, writes event; no scheduler |
| marketops_propose | run_id, record_id, stage, kind, entity_key, facts, optional artifact_ids | Candidate venue/account/person/route, a structured research_brief observation (whose `minimum_targets` is optional — omit or `null` when the user asked for no quantity) or a review disposition in local SQLite. Cannot set verified, ready, approved or send_authorized |
| marketops_status | run_id, optional limit, cursor, kind, entity_key | Reads persisted candidates through the workflow page contract: a page fixes a row high-water mark, later appends land in a new query, and a cursor belongs to its Run and filters. One status isn't full-market coverage |
| marketops_next | run_id, optional limit (default 25) | Bounded structural next-step batch from the workflow core: recommended stage/skill, missing-fact list, `quantity_target_status` and `disposition_counts`. A `HOLD` target is an unresolved gap; a justified `EXCLUDED`/`DUPLICATE` is resolved but never counts toward an explicit positive minimum, and an all-excluded sample is not a successful positive delivery. It recommends work; it grants no spending authority and is not a fact or SOP verdict |
| marketops_events | run_id | Reads bounded recent journal receipts from this Run |
| marketops_note | run_id, note_id, stage, category, problem, action_taken, observed_result, evidence_ref | Appends one de-identified bottleneck note and refreshes the collectable experience file. Rejects emails/URLs/keys/cookies; has no field for hidden reasoning |
| marketops_export | run_id, output_path | Legacy candidate-only UTF-8-BOM CSV utility; **not part of the main workflow contract** and not a confirmed XLSX |
| marketops_export_xlsx | run_id, delivery_id | The explicit workbook-only path: one immutable 9-sheet candidate workbook, per-sheet CSV counterparts and a SHA-256 MANIFEST.json |
| marketops_experience_draft | run_id, scope, rule_version, output_path | Writes unapproved experience.json from persisted incidents/events under approved experience directory |
| marketops_handoff | run_id, operation_id | **The primary final path.** Exactly one research snapshot — the workbook — then the unapproved experience draft and the configured summary refresh. A failed draft or summary refresh is PARTIAL/HOLD carrying partial_reasons, and the receipt still names the workbook that really exists. Immutable path conflicts stay errors |

All real network calls require an existing approved Run, client-set data root, explicit network on and two independent cumulative attempt limits:
- Search: MARKETOPS_SEARCH_ALLOW_NETWORK=1 and MARKETOPS_SEARCH_MAX_CALLS >0.
- Crawl4AI/TinyFish/Jev: MARKETOPS_RESEARCH_ALLOW_NETWORK=1 and MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS >0.

The limit is an **attempt reservation cap**, not a token/currency budget; users must set provider-level limits too. Only a single explicitly selected provider per operation is invoked, and an unknown-result request is never retried under its old operation_id.

For known webpage acquisition, an operator-approved public HTTPS hostname must match MARKETOPS_FETCH_ALLOWED_DOMAINS. This is a client-side guard, not a full browser network sandbox or defense against all DNS rebinding; the operator must also use machine/network access policies and provider-side protections.

## Staging data and provenance

- state/research-lite.sqlite3 stores Runs, candidate objects and material references. It never becomes the MarketOps production canonical Market Graph, and this Lite server does not authorize formal contacts.
- state/research-events.sqlite3 stores operation events, not raw documents or credentials.
- artifacts/<run_id>/ stores derived provider output and SHA. Material has kind provider_extracted or decision_response; a model-generated answer never becomes a raw website byte snapshot.
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
