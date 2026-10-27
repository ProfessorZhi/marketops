---
name: marketops-research
description: Lead a MarketOps sales-research job (find organisations, venues and the people to reach, delivered as Excel) as a pipeline of pools built by a team of agents with the marketops MCP tools - sources -> pages -> venues -> organisations -> contacts, every stage at once, several agents per stage. Use for any lead, venue, account or contact research request.
---

# MarketOps research: the workflow

The work is building five pools, one after the other in order of dependency but **all at the
same time**. Agents do the work (you and the workers you start with your host's multi-agent
mode); the marketops tools are their power tools and only act when an agent calls them.
Other knowledge, read when needed: **marketops-tools** (the six providers and the tools),
**marketops-knowledge** (business judgment that holds for any market: success, grades, customer, sources, blockers); `references/projects/<line>.md` for one market or line.

```
 SOURCES ──mine──▶ PAGES ──pool──▶ VENUES ──who runs it──▶ ORGANISATIONS ──read, judge──▶ CONTACTS ──▶ Excel
 finders           miners          miners                  websites, researchers          researchers, finish
```

| Pool | File | Built by (lane) | Done when |
|---|---|---|---|
| 1 sources | `source_pool.csv` | finders - `find` lanes | saturated: 3+ sources pooled, the last three each under 5% new organisations |
| 2 pages | `frontier.csv`, `pages/harvest-*.csv` | miners - `source` lane, one source per claim | every source's pages read and pooled |
| 3 venues | `venues.csv` | miners (`pool(group_by=operator)`) | every in-scope venue has the organisation that runs it |
| 4 organisations | `orgs.csv` | miners, websites (`site` lane), researchers' `orgs(add)` | every organisation has its site, or a verdict why not |
| 5 contacts | `contacts/`, `verdicts.csv` | researchers - `sweep` lane, ~10 organisations per claim; finish - `review` | every organisation has graded routes or an agent verdict; spot check >= 90% |

## Pool 1 is never one list

The source pool is a pool. A job built on one list - even a good one, even a pool inherited
from an earlier project - finds only what that list knew, and only the kind of entity it lists:
an organisation list gives no venues, a facility census gives no clubs that rent pitches. So,
before anything else:
- register the client's own lists and any inherited list as sources (`sources(add=...)`);
- open `find` lanes for every layer the brief needs: **a venue census** (when the product is
  used on venues), **organisation lists** (federations, registers, city portals), **operators
  and contractors** (tenders) - `references/projects/<line>.md` lists each market's sources;
- keep the finders running beside everyone else until the pool is saturated.

## Running the job

1. **Tools.** `project(name)["capabilities"]` says what is set up (full / reduced / minimal).
   Every provider is optional; the plugin steps down, never stops. Not installed: run
   `install.sh` (Windows `Install.ps1`), restart (`HOSTS.md`).
2. **Brief.** `project(name, brief={goal, country, language, scenario, grades, exclude,
   routes_per_org, ...})`. Ask the user once, up front, for what is missing (marketops-knowledge
   lists the questions); after that never stop to ask - collect decisions for step 6.
3. **Start the whole team at once** with your host's multi-agent mode - every lane from the
   first minute. Default: finders 1-2, miners 1-3, researchers 6-10, websites 0-1, finish 1-2.
   Start line: `You are the <role> worker on MarketOps project "<project>". Read
   references/roles/<role>.md of the marketops plugin and work until your lane is empty.`
   (Claude Code: `marketops-worker` subagents in the background; Codex / WorkBuddy: their
   multi-agent mode, else one session per worker; no multi-agent: work the lanes in turn.)
4. **Lanes feed themselves.** `source`, `site` and `sweep` refill from the pools when claimed;
   you add `find` lanes for gaps (`tasks(action="add", kind="find", title="find: <layer>,
   <region>")`), `dig` packages for a second look, `review` for rows to check.
5. **Steer every 10-15 minutes** from `project(name)["flow"]`: where work waits between pools,
   move agents there. Sources found and nobody mining: another miner. Organisations arriving
   faster than read: more researchers. Pool saturated: finders become miners or researchers.
   A source under 5% A/B after 30 organisations: drop it (`sources(set=[...])`).
6. **One numbered list for the user**: `project()["needs_you"]` plus what workers raised, in one
   message.
7. **Deliver** when the lanes are empty, the 100-row spot check is >= 90% and the 对账 line adds
   up: `export(project)`; then the `learn` command.

## Rules

- Every address comes from a page read with the tools (`fetch`, `harvest`, `run`) and keeps
  its page and quote. Never guess one. The host's own web tools are for looking only.
- One claim, one writer. Feed the pools behind you: a new list -> `sources(add)`, a venue's
  real operator -> `orgs(add)`.
- Judge by what is on disk (rows, pages, the workbook), never by a status line.
- Nothing useful is dropped: review and X are labels. Research only; nothing is sent.
