---
name: marketops-providers
description: Router for the five research providers (Serper, SerpApi, Crawl4AI, TinyFish, Jev) behind the MarketOps MCP - which one each step uses, the escalation ladder, cost and budget, and failure handling. Load the matching provider skill only when you call it.
---

# Five providers, one ladder

Every call goes through MarketOps tools: material is saved in the Run, counted in one shared ledger
and deduplicated across windows. Each call takes `run_id` and a fresh `operation_id`; retrying the
same logical step reuses its id.

In a normal run all five work, each where it is best:

| Job | Who does it | How |
|---|---|---|
| Find targets | Serper (Google web) + SerpApi (`google_maps` listings) + TinyFish search (its own index) | `marketops_search(provider=serper/serpapi/tinyfish)`, `marketops_serpapi_mcp_search`; union the three, each finds sites the others miss |
| Read sites | Crawl4AI (local browser, free) first | inside `marketops_research_step` / `site_harvest` / `harvest_batch` |
| Read what Crawl4AI cannot | TinyFish fetch (free): PDFs, pages Crawl4AI guesses are blocked, empty JS pages | automatic second rung; PDFs go straight to it |
| Judge at volume | Jev: scope triage of the pool, role tag of every email | `marketops_jev_triage`, `harvest_batch(roles_wanted=...)` |
| Hard sites (login, heavy JS), only when authorised | TinyFish browser Agent (paid) | ask once, with the expected yield |

If a run report shows one provider unused, say why (not configured, failed, not needed).

Composite tools already walk the ladder: `marketops_research_step` (one page or search),
`marketops_site_harvest` (one organisation's own site), `marketops_harvest_batch` (a pool batch).
Hosts where the local rung failed and TinyFish succeeded start at TinyFish automatically next time.

## Cost and budget

- Spending tools return `budget_after`: remaining calls and `unknown_held` (calls whose outcome is
  unknown). `unbilled_successes` are normal successes without a vendor bill yet.
- The user's cap is a stop condition: stop and report before the next step would exceed it.
- Cheapest effective path first: one harvest over many fetches, one map listing over many searches,
  saved material over refetching, Jev over reading pages yourself.

## When a call fails

- Challenge page, login wall, 401/403/429: stop that site for this Run; never switch identity or
  proxy to get past it. Use another public source or HOLD.
- Local browser down: the step falls through to TinyFish fetch; tell the user once.
- Timeout / `UNKNOWN`: do not resend blindly; choose another step.
- `NO_NEW_PERSISTED_RECORD_AFTER_ATTEMPT`: change source or target; never repeat the same call.

Every wall is recorded automatically; `marketops_lessons_report` shows what keeps blocking you.
Keys and accounts: [onboarding](../../references/PROVIDER_ONBOARDING.md).
