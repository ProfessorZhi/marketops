---
name: marketops-providers
description: How to use the five providers (Serper, SerpApi, Crawl4AI, TinyFish, Jev) through the MarketOps MCP - which one for each research step, how results pass from one tool to the next, cost tiers and budget control, and what to do when a call fails.
---

# Five providers, one job each

All calls go through MarketOps tools, so every result is saved as material in the Run, counted in
one ledger and deduplicated. Every call takes the Run's `run_id` and a fresh `operation_id`; a retry
of the same logical step reuses its id (a repeated id returns the saved receipt, not a new charge).

## Who does what

| Step | Provider | Tool call | Cost tier |
|---|---|---|---|
| Find bulk sources, directories, PDFs, role pages | **Serper** | `marketops_search(provider='serper', query, country, language, run_id, operation_id, mode='web')` | T1 cheap, per request |
| List places in an area with website/phone; other engines | **SerpApi** official MCP | `marketops_serpapi_mcp_search(run_id, operation_id, params={engine:'google_maps', q:'...', ll or location})` | T2 per search |
| Fetch one known public page | **Crawl4AI** local browser → **TinyFish fetch** | `marketops_research_step(task_kind='known_public_url', url=...)` | T0 free (local) / T0 TinyFish fetch |
| Harvest an organisation's own site | Crawl4AI → TinyFish inside one tool | `marketops_site_harvest(...)` | T0, up to `max_pages` fetches |
| Search with automatic fallback | Serper → SerpApi → TinyFish search | `marketops_research_step(task_kind='source_discovery', query=...)` | first that is authorised |
| Classify / rank saved text at volume | **Jev** | `marketops_jev_choice(run_id, operation_id, source_excerpt, question, options, primitive)` | T1 per call (tokens) |
| Click-to-reveal, forms, logged-in reads | TinyFish Agent | not offered by default | T3 paid, ask first |

Effect first: use the provider that gets the right answer, then the cheapest among equals. Claude's
own context is the most expensive resource; let tools do loops and return summaries.

## How results flow between tools

1. Search results come back as saved source records with URLs: pass the official-site URL to
   `marketops_site_harvest`, directory URLs to `marketops_research_step`.
2. Fetches return `artifact_ids` (use `primary_artifact_ids` first: the rendered page). Read them
   with `marketops_material_excerpt(run_id, artifact_ids, queries)`; quote from them in claims.
3. Jev reads a saved excerpt (`source_excerpt` must come from a saved artifact of this Run) and
   returns a choice/probability/score. Use it to sort, filter and pick what to read next.
4. Candidates you write cite those same `artifact_ids`; `marketops_handoff` builds the workbook from
   them. Nothing typed from memory becomes evidence.

## Provider know-how

- **Serper**: Google results as JSON. Good queries name the source type and area
  (`"playing pitch strategy" Essex filetype:pdf`, `site:<domain> "Grounds Manager"`). Modes beyond
  `web` (maps, news, images, scholar, patents, shopping, autocomplete) exist; `page` only on modes
  that paginate. Results are leads, never verified entities.
- **SerpApi**: a different vendor and key from Serper. Through its official MCP any engine is
  available: `google_maps` for venue listings (keep `place_id`/`data_id` as vendor ids, not
  MarketOps keys), `google`, `bing`, `google_news`. Avoid `google_light` when you need real result
  URLs. The same query via REST and MCP is charged once (shared ledger key).
- **Crawl4AI**: local Chromium, no key. Inspect the real page before choosing selectors; follow
  real links and pagination; keep `mailto:` links and staff cards. If the browser is missing, the
  step falls back to TinyFish fetch and tells you; mention it once to the user. HTTP 200 can still
  be a login or challenge page: check the content.
- **TinyFish**: official MCP. `search` and `fetch_content` are the free/cheap path and the
  fallback when Crawl4AI cannot render. Fetched `provider_html` is vendor-cleaned HTML: fine for
  literal quotes, not the original page bytes. The paid browser Agent is separate (wallet, per-step
  billing) and off by default.
- **Jev**: `choice` picks one option (always include `none`/`unknown`); `noul` gives a yes/no
  probability (near 0.5 = unsure, not "medium"); `score` rates against ordered descriptions. Put
  identity, role, document date and the question in clearly separated text. Batch independent
  questions; never let Jev decide a fact, a grade on its own, or a dedupe.

## Cost control

- `marketops_next` returns `source_selection.budget`: remaining search calls, remaining provider
  calls and calls with unknown charge held. Check it before a burst of calls.
- The user's budget cap is a stop condition: when the remaining budget will not cover the next
  planned step, stop and report instead of spending it down blindly.
- Prefer one harvest over many single fetches, one map listing over many searches, and saved
  material over refetching (`marketops_status(run_id, kind="artifact")` lists what you have).
- `UNKNOWN` charge (timeout, interrupted call): do not resend. It stays counted until reconciled.

## When a call fails

- `SOURCE_ACCESS_BLOCKED`, challenge page, 401/403/429: stop that site for this Run. Do not switch
  provider, proxy or identity to get past it. Use a different public source, or HOLD.
- Timeout / `UNKNOWN`: read the receipt, choose another authorised step; never re-submit blindly.
- Budget exhausted: stop provider calls and report.
- `NO_NEW_PERSISTED_RECORD_AFTER_ATTEMPT` from `marketops_next(..., after_attempt=true)`: the last
  attempt added nothing. Change source or target; never repeat the same call.

Keys, accounts and per-vendor details: [provider onboarding](../../references/PROVIDER_ONBOARDING.md),
[routing](../../references/PROVIDER_ROUTING.md) and `references/providers/`.
