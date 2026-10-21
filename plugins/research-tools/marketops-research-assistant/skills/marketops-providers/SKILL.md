---
name: marketops-providers
description: Router for the five research providers (Serper, SerpApi, Crawl4AI, TinyFish, Jev) behind the MarketOps MCP - which one each job uses and in what order, how to spend as little as possible, how to check balances and spend, and what to do when a provider runs out. Load at the start of every research task; load a single provider's skill only when you call it.
---

# Five providers: right job, cheapest first, never stuck

Every call goes through MarketOps tools: material is saved in the Run, counted in one shared ledger
and deduplicated across windows. Each call takes `run_id` and a fresh `operation_id`.

## Who does what, in this order

| Job | 1st | 2nd | 3rd | Notes |
|---|---|---|---|---|
| Find targets (web) | **Serper** `web` | TinyFish search (free) | SerpApi `google` | `marketops_search(provider='all')` runs Serper + TinyFish together; SerpApi steps in by itself when Serper is out |
| List places in an area | **Serper** `maps` | SerpApi `google_maps` | — | SerpApi only where Serper's places are incomplete (it is 250 free searches a month) |
| Read a page / an organisation's site | **Crawl4AI** (local, free) | TinyFish fetch (free) | — | inside `marketops_research_step`, `site_harvest`, `harvest_batch`; you rarely choose |
| Judge many pages (in scope? whose email?) | **Jev** | you, on the short excerpt | — | `marketops_jev_triage`, `harvest_batch(roles_wanted=...)` |
| Hard pages (click-to-reveal, paginated portals) | TinyFish paid Agent | — | — | only if this user said yes at the start (see below) |

Search engines other than Google (Bing, Baidu, Yandex, Naver, Google Jobs) exist only in SerpApi:
use it for those, sparingly.

## Spend as little as possible

- **Reuse before you call**: the same query in the same Run is answered from saved results.
  `marketops_status` / `marketops_material_excerpt` read what you already have.
- **Serper `limit` stays 10**: more than ten results bills a second credit. Page with `page=2`
  only when the first page was all relevant.
- **One whole-list source beats many searches**: an association directory or one map listing per
  town, then `marketops_pool_import`.
- **Harvest, don't fetch page by page**: `site_harvest` / `harvest_batch` read an organisation's
  own contact pages in one call.
- **Jev before your own reading**: a Jev judgment costs about $0.0001 and none of your context;
  reading a page yourself costs thousands of tokens. Ask Jev only about targets still in scope
  (skip ones already excluded), one question per page, and read the source yourself only for rows
  Jev marks `needs_review`.
- **Stop a method that does not pay**: test a source on ~30 targets; under 5% hit rate, switch.

## Know the balance and the spend

- **At the start of a task**: `marketops_costs(action='balances')` reads SerpApi and TinyFish
  accounts (free) and shows Serper/Jev from the last balance the user entered. Tell the user in one
  line what is left. `marketops_setup_status` lists `low_balance_warnings`.
- **If Serper or Jev show no balance**: ask the user once to read it from the console and record it:
  `marketops_costs(action='set_balance', provider='serper', left=<n>, unit='credit')`.
- **At the end**: `marketops_handoff` returns `run_cost`; put it in the report (per provider, total,
  and which figures are estimates). `marketops_costs(action='report')` gives every Run.
- If the user pays a different price (another SerpApi plan, a Serper bulk rate), record it:
  `marketops_costs(action='set_rate', provider, capability='search', usd_per_unit, plan)`.

## When a provider runs out or slows you down

The plugin recognises "not enough credits", 402 and 429. The provider is then marked out
(SerpApi until the 1st of next month, others a day; a rate limit for a minute) and the next provider
takes over automatically; you will see `provider_health`, `substituted` or `providers_out` in
results.

- Keep going with the next provider; do **not** stop the task or ask.
- Search with Serper and SerpApi both out: TinyFish search (free, ~500 an hour) carries on alone.
- Jev out: role tags come back `ungraded:...`; judge from the short contact context in the harvest
  result (not whole pages) and say in the report that judgment moved to you.
- Write it down: `marketops_note(category='provider_blocked', ...)` and, in the report, which
  provider ran out when, who replaced it, and what the user should top up.
- After the user tops up: `marketops_costs(action='balances')` (or `set_balance`) lifts the mark.

## TinyFish paid Agent: ask each user, default off

At the start of a task ask once, in the user's language: "Some sites only show contacts after
clicking or paging. TinyFish's paid browser agent can do that (about $0.016 per step, your wallet).
Allow it for this task? If yes: how many targets at most, and a total USD limit?" Record the answer
in the brief as `hard_case: {allowed, max_targets, max_steps_per_target, max_usd}`; default
`allowed: false`. In this release the paid Agent stays switched off in the plugin even when allowed:
list those targets in the report as "would need the paid agent" with the reason, so the user can
decide later. Never use it for CAPTCHAs, challenge pages, logins that are not the user's own, or
anything a site forbids.

## When a site refuses

- Challenge page, login wall, 401/403: stop that site for this Run; never switch identity or
  proxy to get past it. Use another public source or HOLD.
- Local browser down: the step falls through to TinyFish fetch; tell the user once.
- Timeout / `UNKNOWN`: do not resend blindly; choose another step.

Every wall is recorded automatically; `marketops_lessons_report` shows what keeps blocking you.
Keys and accounts: [onboarding](../../references/PROVIDER_ONBOARDING.md).

## Before the first page read: is Crawl4AI working?

Crawl4AI is the reader every harvest depends on. Do not silently replace it with something else.

- `marketops_setup_status` shows `provider_readiness.providers.crawl4ai`. Anything other than
  `READY` means acting before the research starts.
- `INSTALLED_NOT_CHECKED`: run `marketops_crawl4ai_check(run_id=<this run>)`. It starts the
  browser once and reads one test page.
- `NOT_INSTALLED`, `NOT_WORKING` or `LOCAL_DOWN_...`: tell the user what the check found, then
  offer the repair (`marketops_crawl4ai_check(repair=true)`, or Repair.cmd / repair.sh in the
  MarketOps folder).
- Go ahead with TinyFish fetch only if the user explicitly agrees. Write it in the report.
- Any answer carrying `crawl4ai_down` mid-task means the same thing. Pause and ask the user;
  do not carry on quietly.
