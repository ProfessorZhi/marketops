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

## What makes the work good

Five things decide a run; each has a reason and a place where the plugin holds it for you.
Under all five: **the plugin makes agents stronger; it does not script them.** The agents
choose the lists, mine them, judge whose an address is, dig, scale or drop a source; the tools
do the bulk work an agent asks for in one call (read ten sites, page through a list, grade a
batch) and hand back what they saw, with advice. A tool never researches alone: `run` without
`orgs` only reads sites ahead of the researchers, stops starting new ones once 30 wait for an
agent (`waiting_for_agents`), and ends after an hour with no agent taking work. Paris 071 let a
script dig six rounds alone for six hours: 15 organisations with A/B for 105
organisation-hours, most addresses wrongly shared. "What the tools give you" below lists each
default and how you overrule it.

- **Pipeline - stages hand over through pools, never through messages.** A stage takes what is
  on disk in the pool before it and writes to the pool after it, so no stage waits for another to
  finish and nothing is lost between them. Handovers in chat were Bruce's costliest failure (a
  middle layer built and never written back). `project()["flow"]` shows what waits between pools.
- **Concurrency - every stage at once, several agents per stage, one writer per claim.** Finding
  lists, mining them, finding sites and researching people overlap from the first minute; inside
  a lane each agent owns its claim (one source, ten organisations) end to end. Splitting one
  package across many hands made Bruce's runs slower (coordination, not crawling, became the
  cost); more claims in parallel made them faster.
- **Many sources - coverage comes from the union of lists, not from one good list.** Each source
  knows a part of the market and one kind of entity; saturation (the last sources mostly repeat
  what is pooled) is the only honest sign the pool is complete. And every pooled source must be
  *researched*, not only pooled: the research lanes give each one a turn (Paris 072 pooled 27
  sources, then four researchers spent forty minutes on the first one; 26 were never read).
- **Speed - more organisations in flight, cheap tools before expensive ones.** Throughput is
  agents x claims, not pages per site; the tools race providers, batch requests, cache every page
  and search for every project, and the lightest tool goes first (search -> fetch -> Agent). Never
  poll a status; work, and look at `flow` every 10-15 minutes.
- **Quality - evidence on every row, judged at the source, checked before delivery.** Every
  address keeps its page and quote; grades follow one table; weak sources are dropped after 30
  organisations, not after the run; a 100-row spot check gates delivery. A missing route is better
  than a wrong one - and a status line is never evidence.

## Pool 1 is never one list

The source pool is a pool. A job built on one list - even a good one, even a pool inherited
from an earlier project - finds only what that list knew, and only the kind of entity it lists:
an organisation list gives no venues, a facility census gives no clubs that rent pitches. So,
before anything else:
- register the client's own lists as sources (`sources(add=...)`); an earlier project's pool
  is reused only when the user asks for it (a fresh project flags anything brought in);
- open `find` lanes for every layer the brief needs: **a venue census** (when the product is
  used on venues), **organisation lists** (federations, registers, city portals), **operators
  and contractors** (tenders) - `references/projects/<line>.md` lists each market's sources;
- keep the finders running beside everyone else until the pool is saturated.

## What the tools give you (and what stays yours)

| The tools, by default | Why | Your call |
|---|---|---|
| Research lanes (`sweep`, `dig`, `site`) hand out organisations source by source in turns; each source gets a pilot of 30, then more only while it yields A/B (>= 5%) | Bruce: test a source on 30, scale at >= 10%, stop under 5%; 072 researched one source of 27 | `sources(set=[{source, status: "scale"}])` gives a source everything, `"dropped"` nothing; `flow.sources_take_turns` shows each pilot. You may claim or `tasks(action="add")` any package |
| A list row that prints an address gives it to that row's organisation as a contact, quoted from the row | school, club and timetable lists carry mailboxes; 072 threw away 1,658+ of them | map `email` / `person` / `role`; grade or correct as always |
| Organisations match on their registry number (UAI, SIRET, RNA) before name and town | one club became two when one list wrote `75011` and another `Paris` | map the number when the file has one; merge or split by hand with `orgs(set=...)` |
| `run` without `orgs` reads sites ahead of researchers, never digs, waits once 30 are waiting | 071 | dig rounds for your own package: `run(orgs=[...], rounds=1-2)` |
| `browse` (the paid TinyFish Agent) is off; a call records the page and the user is asked once for all of them | it costs per step | the user allows it in the brief (`paid_agent: true`) |
| A fresh project (`brief.fresh`) reuses nothing other projects read or learned, and flags files nobody fetched or registered here | a test must start from zero; 072b began from a 0.6.6 pool | without `fresh` the shared library makes reruns cheap |
| Every tool call goes to the project's `calls.jsonl` (agent, tool, arguments in brief, seconds, errors) | the whole chain is reviewable without transcripts | read it in the retrospective |

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
4. **Lanes feed themselves.** `source`, `site`, `sweep` and `dig` refill from the pools when
   claimed (`dig`: organisations the tools read without a confirmed A/B);
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

## Keep the record

Every run is also a test of the plugin, so the work leaves its own trail - written while it
happens, never reconstructed afterwards:
- **The tools log themselves**: every call goes to `calls.jsonl` (who, which tool, how long,
  what failed). Pass `agent="<your name>"` on every call that takes it, so the log can tell the
  workers apart.
- **Agents write what the log cannot see**: `project(name, journal={"type": "blocker" | "smooth" |
  "decision" | "idea", "text": "...", "evidence": "file / count"}, agent="<name>")` - the moment a
  tool or a site stops you and how you got round it, a source that paid or did not, a decision
  others should know (dropping a source, scaling one, changing the scope). One line each; a
  worker writes at least its blockers, the lead its decisions.
- **The lead closes it**: before `export`, one `{"type": "retro"}` entry - what went well, what
  cost the most, what the plugin should do differently. `export` writes `RETRO.md` from all of it
  (numbers, the tool-call table, the journal), addresses masked: the file to read for the next
  plugin change.

## Rules

- Every address comes from a page read with the tools (`fetch`, `harvest`, `run`) and keeps
  its page and quote. Never guess one. The host's own web tools are for looking only.
- One claim, one writer. Feed the pools behind you: a new list -> `sources(add)`, a venue's
  real operator -> `orgs(add)`.
- Judge by what is on disk (rows, pages, the workbook), never by a status line.
- Nothing useful is dropped: review and X are labels. Research only; nothing is sent.
