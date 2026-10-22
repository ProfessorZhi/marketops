---
name: marketops-research
description: Single entry for a MarketOps research task (venues, organisations, key contacts, any market). Runs autonomously from brief to audited XLSX using the MarketOps MCP tools, five providers and, where the host supports it, parallel subagents.
---

# MarketOps Research

Goal: the user does as little as possible; you run the whole task in long autonomous stretches and
deliver an evidence-backed workbook. Effect first, cost second.

## Hard rules (read first)

1. **Only the `marketops_*` tools.** Do not use Bash, Read, Grep or Python on the plugin's code,
   its SQLite databases or its data directory. Do not use the host's WebSearch/WebFetch.
2. **A refusal tells you the fix.** Write caveats such as `QUOTE_NOT_IN_ARTIFACT` show the closest
   text on the page: copy a verbatim 12-600 character span from it and resubmit with a new
   `record_id` and `facts.supersedes`. If it cannot be fixed in two tries, record HOLD and move on.
   Never reverse-engineer the gate.
3. **Deliver only with `marketops_handoff`.** Never write an XLSX or CSV yourself.
4. **Keep going, then finish.** While budget and in-scope targets remain, research the next target
   instead of asking whether to continue. If you see two ways forward, do the one with the better
   expected yield now and name the other in the report; never end a turn with "which do you want?"
   while budget remains. A blocked site is not a question for the user: use another source. Stop
   only when the agreed target work is covered, the authorized budget is exhausted,
   the user asks to stop, or a verified blocker prevents further authorized work.
   A turn/context warning or a 40-second tool slice is not a completed task: save
   a checkpoint and retain the same Run/queue for continuation.

## 1. Start (ask once)

- First use only: `marketops_setup_status`; if not configured, follow `marketops-setup`.
- Inspect its `provider_readiness.shared_request_budget` before new network work.
  Vendor credits do not override an exhausted local request limit; report that limit
  before claiming the task can run, and never reset the ledger or increase the cap yourself.
- `marketops_update_check()`: if a newer release exists, tell the user in one line (it says how
  to update: Update.cmd, then restart the agent) and carry on with the task.
- `marketops_runs(limit=10)`: resume an existing Run for the same task instead of starting over.
- `marketops_next(run_id)` includes `execution`. Follow its `next_action`; do not
  give a final research answer while `execution.continuation_required=true` and
  authorized work can still proceed. A saved workbook can be a checkpoint.
- Load `marketops-providers` (which provider for which job, cheapest first, what to do when one
  runs out) and call `marketops_costs(action='balances')`; tell the user in one line what each
  account has left.
- Crawl4AI must be working: check `provider_readiness.providers.crawl4ai` in
  `marketops_setup_status`. If it is not `READY`, follow "Before the first page read" in
  `marketops-providers`. Put the repair offer, or the question about going on with TinyFish
  only, into the same single message as the questions below.
- Ask **once**, in one message, only for what is missing: scope (area, entity type, roles wanted),
  what the user sells and who buys it (customer vs competitor), delivery format, budget cap, and
  whether TinyFish's paid browser agent may be used for hard pages (default no; see
  `marketops-providers`). Do not ask for anything the user already said.
- `marketops_start(run_id, scope, objective, rule_version, brief={record_type:'research_brief',
  target_kind:'venue'|'organization', goal:'pool'|'contacts', require_named_person:false,
  roles_wanted:[<the roles the user named>], hard_case:{allowed:<the user's answer, default false>},
  minimum_targets:<the user's number or null>, evidence_requirements:{organization:['identity']}})`.
  Details: `marketops-discovery`.

## 2. Run the funnel (no check-ins)

| Stage | What you do | Main tools / providers |
|---|---|---|
| Source pool | find bulk sources and the full list of targets; note each source's listed count | Serper search, SerpApi `google_maps`, `marketops_research_step` (Crawl4AI → TinyFish) |
| Venue / org pool | dedupe, exclude out-of-scope types, keep reasons | `marketops_pool_import`, `marketops_jev_triage` |
| Accounts | for each organisation: `marketops_site_harvest` on its own site, then judge role and grade from each route's context | Crawl4AI → TinyFish fetch inside the tool, Serper `site:` role search |
| Evidence | every value has a literal quote from a saved page; the tools enforce this | `marketops_propose_batch`, `marketops_evidence_audit` |
| Delivery | reconcile counts, verify a sample, hand off once; report spend per provider from `run_cost` | `marketops_handoff`; see `marketops-delivery` |

**Contacts goal: go deep before going wide.** When the user wants contacts (`goal:'contacts'`):
1. Shortlist 20–30 of the most likely targets from the pool: own site known, the right type, and
   in scope.
2. Run accounts on those to delivery, so the user gets usable people and emails early.
3. Only then widen the pool.

A pool of 300 names with no contacts does not meet a contacts goal. The Paris run did
exactly that: 0 venues and 0 people delivered.

**When a method finds little, switch methods; never stop.** Contacts are found by a ladder of
methods, and `marketops_method(run_id)` says which one is next:
1. Own site.
2. Operator: the municipality, school, club or company that runs the venue.
3. Role searches.
4. Official directories.
5. Documents such as minutes, tenders and organisation charts.
6. TinyFish agent, only if allowed.

After a method has been tried on about 20–30 targets with little result:
1. Record it: `marketops_method(run_id, method, outcome='exhausted', note='<counts>')`.
2. Apply `next_method` to the remaining targets right away, in the same turn.

The Paris continuation stopped with 70 targets pending and $1.93 left because the own-site
method found nothing. That was the wrong move: "no email on the venue's site" does not mean the
contact does not exist. Public venues are usually run by an operator whose staff are listed
elsewhere. Keep the inventory going (paging the official list) while contacts are researched;
one does not stop the other.

An operator shortlist is also not a venue inventory. For a region-wide venue task,
build the facility list from the scoped official sources, retain venue addresses,
and map venues to operators; a chain with many sites is not one venue. Researching
the first 20–30 operators is a first tranche, not permission to end the whole task.

Load `marketops-discovery`, `marketops-accounts`, `marketops-providers` or `marketops-delivery`
only when you reach that stage. Past runs' pitfalls, by stage: [lessons](../../references/MEMBER_A_LESSONS.md). Use `marketops_next(run_id)` to see gaps and the suggested next action.

**Large tasks (over ~50 targets) or several windows**: load `marketops-orchestrate`. Targets go
into a shared pool on this machine's data directory; worker windows claim batches with
`marketops_harvest_batch` and never collide. The scene pack for the market (e.g.
[UK natural grass](../../references/ACCEPTANCE_PROFILE_UK_GRASS.md)) supplies roles, grades,
exclusions, authority sources and query templates.

**Parallel accounts.** If the host can start subagents (Claude Code: `account-researcher`), give
each subagent 1–3 organisations with `run_id`, published name, `organization_key` and website, in
batches of 5–10 subagents. You dispatch and merge; they harvest. Hosts without subagents (Codex,
WorkBuddy) run the same account procedure from `marketops-accounts` one organisation at a time.

## 3. When to stop and ask

**Do not end your turn while `execution.continuation_required=true`.** A saved workbook or a
checkpoint handoff is not the end. If a reply carries `do_not_stop` or `execution.turn_rule`,
carry out its `next_action` and `next_method` straight away, without an interim summary.

The research ends only when one of these holds:
- the budget is spent;
- the goal is met;
- `execution.state` is `FINAL_METHODS_EXHAUSTED` (every method recorded as exhausted);
- one of the reasons below for asking the user.

When it does end, say which of these it was.

Ask the user only when: the budget cap is about to be reached; a paid tier (TinyFish Agent) is needed but not
authorised; the same rule conflict keeps recurring (many `RULES_UNKNOWN`); a credential or
configuration is missing; Crawl4AI stopped working (`crawl4ai_down`) and the user has not yet
agreed to TinyFish-only reading. Everything else: pick another authorised source or record HOLD with the
reason, and continue with the next target. Report all of it at the end.

`PAGE_FAILURE_ONLY` and successful per-page fallback do not trigger a new permission
question. Pause for a local outage only when `crawl4ai_down` is present or the step's
`local_reader_health.tinyfish_only_approval_required` is true. If uncertain, run the
self-check before telling the user that the computer's reader is broken.

## 4. Non-negotiables

- Never guess an email or a relationship; a value without a literal quote from a saved page stays HOLD.
- Never bypass a challenge page, login or block; record `SOURCE_ACCESS_BLOCKED` and move on.
- Every target gets a result: admitted routes, or HOLD/EXCLUDED with the reason and pages tried.
- Do not use the host's own WebSearch/WebFetch for research: evidence and budget only exist inside
  the MarketOps tools.
- Research only. No outreach, form submission or sending.

## 5. Experience

`marketops_start` and `marketops_next` return a few relevant lessons; `marketops_experience_recall`
gives more for a stage or provider. They are advice, not rules. After using one, report the outcome
with `marketops_experience_feedback` (helped / not_helped / contradicted / unknown, with an
`artifact:` or `event:` reference from this Run). Record every blocked source, provider failure and
user correction with `marketops_note` the moment it happens (no URLs, emails or secrets), and end
every session with `marketops_feedback_pack`; the method is in `marketops-feedback`.

## 6. Do-not-contact list

Before the first handoff of a task, call `marketops_suppression(action="status")`. If the user has
a No-Go / unsubscribe / complaint list (a sheet, a pasted list, an earlier export), add it with
`marketops_suppression(action="add", entries=[...], reason=..., source=...)`; matching contacts are
then kept out of every delivery from every conversation. If no list exists, say so in the report
(`NO_LIST`): never describe an unchecked delivery as clean.
