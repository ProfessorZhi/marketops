---
name: marketops-providers
description: Router for the five research providers (Serper, SerpApi, Crawl4AI, TinyFish, Jev) behind the MarketOps MCP - which one each job uses and in what order, how to spend as little as possible, how to check balances and spend, and what to do when a provider runs out. Load at the start of every research task; load a single provider's skill only when you call it.
---

# Five providers: right job, cheapest first, never stuck

**Budget = red lines.** `marketops_costs(action='budget')` shows, for each account:
- how much is left now (from the freshest balance reading);
- its red line, the amount research must leave in the account (default 10% of the
  balance at setup);
- how much can still be used above the red line.

Rules:
- Spend freely above the red line. Free Crawl4AI and TinyFish search/fetch cost nothing.
  The total red line (default $500) is a safety stop.
- An account at its red line (`BUDGET_PROVIDER_CEILING`, dimension `red_line`) switches
  provider or method; it never stops free research.
- **Limits and red lines can change mid-Run; never answer "it cannot be raised".**
  - Before a Run the owner sets them in local setup (`--limit PROVIDER=AMOUNT`,
    `--total-usd`).
  - When the user says to use more (e.g. "SerpApi 有余额，允许继续使用", "本 run 允许多配置额度"):
    - `marketops_costs(action='set_limit', provider=..., limit=<new amount in its unit>,
      user_words='<their words>')`;
    - leave out `provider` for the total, in USD;
    - and, if the account is at its red line, `action='set_red_line', keep=<amount to leave>`.
  - Either action turns an older fixed-share (30%) policy into red lines.
  - Raising needs the user's words and is recorded; lowering never does.
  - Do not open a new Run or reset anything to get round a limit.
- Balances read automatically for SerpApi, Serper and TinyFish
  (`marketops_costs(action='balances')`). Jev has no balance API: enter the console figure
  once with `set_balance`, or, when the user gave a request allowance instead ("用 300 次",
  "你自己注意下额度"), `set_limit(provider='jev', limit=<requests>, user_words=...)`: Jev then
  runs until that many requests and stops. Without either, Jev cannot spend at all.
See [monetary budget details](../../references/LONG_RESEARCH_AND_BUDGETS.md).

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
- **If Serper or Jev show no balance**: read `marketops_accounts()`; only for
  `ask_balance_once=true`, claim `marketops_accounts(action='ask_balance', account_ref=<ref>)`
  and ask if it returns `ask_now=true`. Record the console's current available amount using
  `marketops_costs(action='set_balance', provider='serper', account_ref=<ref>, left=<n>, unit='credit')`
  (Jev dollars: `provider='typesafe_jev', unit='USD'`). If unknown, continue recording consumption
  without repeating the question in later conversations. Never subtract historical dashboard
  spend from a current balance; report snapshot time, local estimate and unknown exposure.
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

Balances belong to accounts; keys from one account share a wallet. Jev can select the next
locally authorised, enabled billing account after quota exhaustion; a sent request is not replayed.
429 cooldown, 401/403, timeouts and UNKNOWN charges never rotate identities. Changing accounts
does not reset project, Run or provider caps. Serper remains one billing account. No account
creation for promotional credits or restriction avoidance. Local setup and exact semantics:
[accounts and balances](../../references/ACCOUNT_BALANCES.md).

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
- `INSTALLED_NOT_CHECKED` or `BROWSER_READY_PAGE_UNCHECKED`: run `marketops_crawl4ai_check(run_id=<this run>)`. It starts the
  browser once and reads one test page.
- `RESTART_REQUIRED` / `CACHE_RESTART_REQUIRED`: restart the agent/MCP server. The SDK
  was initialized before its managed cache; reinstalling Chromium does not fix that.
- A browser-only `WORKING` result is not a page-read pass. Require
  `page_read_verified=true`; `SKIPPED_POLICY` means the page was not tested.
- `NOT_INSTALLED`, `NOT_WORKING` or `LOCAL_DOWN_...`: tell the user what the check found, then
  offer the repair (`marketops_crawl4ai_check(repair=true)`, or Repair.cmd / repair.sh in the
  MarketOps folder).
- Go ahead with TinyFish fetch only if the user explicitly agrees. Write it in the report.
- Any answer carrying `crawl4ai_down` mid-task means the same thing. Pause and ask the user;
  do not carry on quietly.
- A failed page is not a confirmed local outage. When a step reports
  `local_reader_health.tinyfish_only_approval_required=false`, continue the admitted
  per-page fallback; do not ask the user to approve it again. Domain memory starting
  a host at TinyFish does not establish that Crawl4AI is broken on this computer.
- For repeated `PAGE_FAILURE_ONLY`, run the Crawl4AI self-check before claiming a
  computer-wide failure. Report the attempt's `local_runtime_diagnostic` when present.
- Balance and admission are separate: inspect the shared request budget as well as
  vendor credits. Do not describe a provider as unusable because the local request
  ceiling was exhausted; report `BUDGET_PROJECT_CEILING` and the configured limit.
