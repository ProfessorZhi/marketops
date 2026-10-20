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
4. **Always finish**: every run ends with `marketops_handoff` and a short report, even when partial.
   Budget your turns: stop researching when about 10 turns remain and hand off.

## 1. Start (ask once)

- First use only: `marketops_setup_status`; if not configured, follow `marketops-setup`.
- `marketops_runs(limit=10)`: resume an existing Run for the same task instead of starting over.
- Ask **once**, only for what is missing: scope (area, entity type, roles wanted), delivery format,
  budget cap. Do not ask for anything the user already said.
- `marketops_start(run_id, scope, objective, rule_version, brief={record_type:'research_brief',
  target_kind:'venue'|'organization', goal:'pool'|'contacts', require_named_person:false,
  minimum_targets:<the user's number or null>, evidence_requirements:{organization:['identity']}})`.
  Details: `marketops-discovery`.

## 2. Run the funnel (no check-ins)

| Stage | What you do | Main tools / providers |
|---|---|---|
| Source pool | find bulk sources and the full list of targets; note each source's listed count | Serper search, SerpApi `google_maps`, `marketops_research_step` (Crawl4AI → TinyFish) |
| Venue / org pool | dedupe, exclude out-of-scope types, keep reasons | `marketops_pool_import`, `marketops_jev_triage` |
| Accounts | for each organisation: `marketops_site_harvest` on its own site, then judge role and grade from each route's context | Crawl4AI → TinyFish fetch inside the tool, Serper `site:` role search |
| Evidence | every value has a literal quote from a saved page; the tools enforce this | `marketops_propose_batch`, `marketops_evidence_audit` |
| Delivery | reconcile counts, verify a sample, hand off once | `marketops_handoff`; see `marketops-delivery` |

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

Only: the budget cap is about to be reached; a paid tier (TinyFish Agent) is needed but not
authorised; the same rule conflict keeps recurring (many `RULES_UNKNOWN`); a credential or
configuration is missing. Everything else: pick another authorised source or record HOLD with the
reason, and continue with the next target. Report all of it at the end.

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
`artifact:` or `event:` reference from this Run). Record a blocked source, a provider switch or a
user correction with `marketops_note` (no URLs, emails or secrets).
