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

## Other tools: stage progress and memory

| Tool | Required input | What it really does |
| --- | --- | --- |
| marketops_capabilities | none | Read configured flags, installed SDK hints and current limits; no network |
| marketops_start | run_id, scope, objective, rule_version | Creates/resumes one durable local Run, writes event; no scheduler |
| marketops_propose | run_id, record_id, stage, kind, entity_key, facts, optional artifact_ids | Candidate venue/account/person/route or review observation in local SQLite. Cannot set verified, ready, approved or send_authorized |
| marketops_status | run_id, optional limit | Reads real persisted run records and artifacts. One status isn't full-market coverage |
| marketops_events | run_id | Reads bounded recent journal receipts from this Run |
| marketops_export | run_id, output_path | Writes candidate-only UTF-8-BOM CSV within approved data root exports directory; not a confirmed XLSX nor an official CRM update |
| marketops_experience_draft | run_id, scope, rule_version, output_path | Writes unapproved experience.json from persisted incidents/events under approved experience directory |
| marketops_handoff | run_id, operation_id | One candidate-only export operation: save a CSV and an unapproved incident experience draft to customer runtime directories, record HOLD on partial failure; **not verified contacts or an XLSX file** |

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

`marketops_export_xlsx(run_id, delivery_id)` writes immutable `Research_Result.xlsx` with eight business/audit worksheets, eight matching CSV files and an actual-file SHA-256 `MANIFEST.json`. It directly reads the same Lite SQLite candidate store and verifies source bytes, cross-run references, XLSX sheet header/row shapes, CSV rows and file hashes. It deliberately **does not** promote candidates to approved customer accounts or A/B/C/D ContactRoutes. See [E2E_VERIFICATION](E2E_VERIFICATION.md).


## XLSX tool added (2026-10-16; code candidate)

`marketops_export_xlsx(run_id, delivery_id)` emits `Research_Result.xlsx` (8 candidate business/audit sheets), CSV counterparts and `MANIFEST.json`. It validates source material SHA-256 by rereading actual bytes and reopens ZIP/XML/CSV before returning. All formal grades stay `RULES_UNKNOWN`; the Lite store cannot independently approve A/B/C/D or outreach. See [E2E_VERIFICATION](E2E_VERIFICATION.md).
