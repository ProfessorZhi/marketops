---
name: marketops-research
description: Single entry for a MarketOps research task (venues, organisations, key contacts, any market). Runs autonomously from brief to audited XLSX using the MarketOps MCP tools, five providers and, where the host supports it, parallel subagents.
---

# MarketOps Research

Goal: the user does as little as possible; you run the whole task in long autonomous stretches and
deliver an evidence-backed workbook. Effect first, cost second.

Read [long research and monetary envelopes](../../references/LONG_RESEARCH_AND_BUDGETS.md).
In MONEY mode, request/page counters are telemetry, not a spending ceiling.
Read `marketops_costs(action='budget')`; do not interpret a null request limit as
zero. Free local Crawl4AI remains available when a paid supplier envelope closes.
Never renew or widen an envelope just to continue. Legacy REQUEST_COUNT mode
keeps its explicitly approved cap until local setup changes the policy.

For long regional discovery, save each upstream JSON page once and use the
artifact mode of `marketops_pool_import` to preview/filter/import it locally.
Never refetch one record per request merely to get around the excerpt window.
Prefer a portal's export endpoint (one call per department, up to 50,000 rows) over
100-row record pages; see `marketops-discovery`.
Follow both local `next_offset` and upstream pagination until reconciled.

For named contacts, ask for desired decision roles rather than an invented
headcount. Put the wanted subset of `management`, `operations`,
`commercial_events` in `brief.role_groups_wanted`. Use
`marketops_next.contact_coverage` to research missing roles from alternative
public sources; a generic inbox or the first named person does not close the
task. Reuse people across venues only through sourced operator relationships.
Pilot batch sizes are method tests, never total target quotas.

Retain `usage_checkpoint.since` returned by `marketops_start` for this session.
Pass it to `marketops_handoff(since=...)`; report `session_cost` separately from
the explicitly cumulative `run_cost`. A new context window keeps that checkpoint.

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
  account has left, distinguishing reported snapshots, local estimates and UNKNOWN. For missing
  Serper/Jev readings, follow the persistent once-only question in `marketops-providers`; do not
  ask again just because this is a new conversation or Run.
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
  minimum_targets:<the user's number or null>, minimum_ab:<A+B people wanted: the user's
  number, "几十到上百"/"dozens to hundreds" = 50; leave out for the default 50>,
  evidence_requirements:{organization:['identity']},
  scene:'<shipped pack id>' or scene_rules:{decision:[...], operational:[...], entry:[...],
  excluded:[...]}})`. A market with no shipped pack is normal: write `scene_rules` in the local
  language (Paris sports venues: decision `directeur des sports`, `responsable des équipements
  sportifs`; operational `responsable de gymnase`, `régisseur`; entry `service des sports`,
  `réservations`; excluded `communication`, `ressources humaines`). Details: `marketops-discovery`.

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

**Widen what pays.** When `marketops_next` says `EXPAND_PRODUCTIVE_SOURCE`, a kind of list
gave A/B (in Paris: committee and federation officer pages, never town or university sites).
Do the same search for the next sport, département, town or federation, import each new list
as its own source and harvest it, round after round, until the A+B goal is met or a round of
10 new lists gives none. This is the work, not an option to offer the user: the first live
Claude Code run named this route, wrote "much room left", and stopped at 12 people.

A pool of 300 names with no contacts does not meet a contacts goal. The Paris run did
exactly that: 0 venues and 0 people delivered.

**A venue inventory gets its contacts through its operators.** Do not pick 20 well-known
brands and call that the contact search. The Paris run did that: 4,737 venues stayed
untouched and only 1 email was found.

Steps:
1. `marketops_venue_groups(run_id, by='operator')`, or `by='city'` with the source's
   municipal operator types for public venues.
2. `import_to_pool=<contacts pool>` puts the largest uncovered groups in the pool.
3. Harvest them with `marketops_harvest_batch`.

Where the named staff are:
- A commune's sports department (direction des sports / service des sports) usually lists
  its director and team on the town site, with work emails.
- Companies list theirs in legal notices, press pages and annual reports.

`execution.venue_operator_groups` shows how many groups are still uncovered. While any
are, the research is not finished. `OPERATOR` cannot be marked `not_applicable` when the
Run holds venues.

**When a method finds little, switch methods; never stop.** Contacts are found by a ladder of
methods, and `marketops_method(run_id)` says which one is next:
1. Own site.
2. Operator: the municipality, school, club or company that runs the venue.
3. Role searches.
4. Official directories.
5. Documents such as minutes, tenders and organisation charts.
6. TinyFish agent, only if allowed.

After a method has been tried on about 20–30 targets with little result (the plugin only accepts
`exhausted` after 20 finished targets or 15 provider calls since the method started, or once
every queued target is finished; otherwise it answers `METHOD_NOT_TRIED_ENOUGH`):
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
`marketops_harvest_batch` and never collide. A shipped scene pack (e.g. `uk-natural-grass`, with
[its profile](../../references/ACCEPTANCE_PROFILE_UK_GRASS.md)) also lists sources in the order
they yielded A+B before.

**Grades and the KPI are computed, not judged.** At handoff the code grades each email route
from the scene rules (A/B need name, role and email in one block of one saved page; a generic or
shared inbox is D; an excluded role is X). `marketops_next` shows `execution.grading`: A+B as
distinct people per 100 finished targets. That number is the KPI; C/D are counted separately and
never presented as key contacts.

**Sources are judged by what they yield.** `execution.source_gate` gives each import source's A+B
rate. After 30 finished targets a source under 5% is parked (its pending targets go to HOLD
`SOURCE_YIELD_STOP`); 5–10% means change the approach for it once. Follow
`execution.source_advice`: a better list for the same target type is worth more than deeper
research on a poor one (past work: 30% vs 3% from two lists of the same kind).

Where named, role-bound emails come from (live Paris run, 215 targets, 1.4 A+B per 100):
- chains and commercial venues (padel, climbing, five-a-side) print one inbox per site: C;
- town and council sites print service inboxes (`sports@`); their staff are named in job
  ads and news without an address: named leads, C routes;
- club and association committee pages ("Bureau", "Comité directeur") print officers with
  their own addresses: most of the A and B;
- a town's list of clubs carries club officers, delivered as listed by the town.
So when the goal is named people, add club and association targets early and let the gate
judge each list. Bruce's UK work found 45% A+B accounts on organisations' own sites.

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

Never end with a list of "next options" the tools already tell you to do; do them. A finding
like "this source works, there is much room left" is a reason to continue, not a report.

When it does end, say which of these it was.

**Public sources exhausted, goal not met.** If it ends as `FINAL_METHODS_EXHAUSTED` with the goal
unmet, say plainly that the public sources are exhausted for this goal. Give the yield of each
method, for example "165 municipal sports departments: 0 personal work emails, only
switchboards and forms". Then offer the user concrete choices in one message:
- accept named people with role but no email;
- accept a department or role mailbox for a named person's team;
- authorize TinyFish Agent for specific hard pages;
- stop here.

Do not keep importing more targets of a kind that has already yielded nothing.

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
