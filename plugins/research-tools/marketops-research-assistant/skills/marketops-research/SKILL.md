---
name: marketops-research
description: Lead or work on a MarketOps sales-research job - find organisations, venues and the people to reach (presidents, secretaries, facility and operations managers) with graded, quoted email routes, delivered as an Excel workbook - as a team of agents building pools with the marketops MCP tools (sources -> pages -> venues -> organisations -> contacts). Use it for any lead, prospect, venue, account or contact research request, any "找客户 / 找联系人 / 找负责人邮箱 / 线索名单 / 场馆调研" job, any smoke or benchmark run of the plugin, and whenever you are a MarketOps worker deciding what to do next - even if the user never says "MarketOps".
---

# MarketOps research: the workflow

The job is five pools, filled in order of dependency but **all at the same time**, by agents.
You (the lead) and the workers you start do the research; the marketops MCP tools are power
tools that act only when an agent calls them - they read ten sites at once, page through a list,
grade a batch, and hand back what they saw. They never decide for you, and a tool left alone
never researches (071 let a script dig for six hours: most addresses wrongly shared).

Read the others when needed: **marketops-tools** (which tool and provider for which job, every
call), **marketops-knowledge** (grades, who the customer is, which sources pay, when to stop a
source), `references/projects/<line>.md` (one market), `references/defaults.md` in this skill
(what each tool does by default and how to overrule it). On a host that does not load skill
files, `guide("workflow" | "tools" | "knowledge" | "role:<role>")` returns the same text.

```
 SOURCES ──mine──▶ PAGES ──pool──▶ VENUES ──who runs it──▶ ORGANISATIONS ──read, judge──▶ CONTACTS ──▶ Excel
 finders           miners          miners                  websites, researchers          researchers, finish
```

| Pool | Built by (lane) | Done when |
|---|---|---|
| 1 sources | finders - `find` | saturated: 3+ sources, the last three each under 5% new organisations |
| 2 pages | miners - `source`, one source per claim | every source read and pooled |
| 3 venues | miners (`pool(group_by=operator)`) | every in-scope venue has the organisation that runs it |
| 4 organisations | miners, `site`, researchers' `orgs(add)` | each has its site, or a verdict why not |
| 5 contacts | researchers - `sweep` / `dig`, ~10 per claim; finish - `review` | each has graded routes or a verdict; spot check >= 90% |

Pools hand over on disk, never through chat: a stage takes what is in the pool before it and
writes to the pool after it, so nobody waits for a stage to finish and nothing is lost between
them (Bruce's costliest failure was a middle layer built in chat and never written back).

## What decides the A/B rate

The share of organisations with an A or B route moves with three things, in this order:
1. **Which lists the pool is built from.** Lists that name people (a facility's booking or
   timetable file, a sports office's yearbook, a federation's club finder) give many times more
   A/B per organisation than lists of names (a company register: 1.7% in 079). A pool is never
   one list: open `find` lanes for every layer the brief needs, and let the pilot (30
   organisations per source, paused under 5% A/B) decide what scales.
2. **Idle minutes.** A worker that ends its turn, or polls an empty lane while the pool could
   grow, costs more than any slow site (079: 26 minutes, A/B fell from 41% to 23%).
3. **An end.** Without a time box the late hours read the weakest sources (078: 41% at 38
   minutes, 37% after three hours).

## The run in time

| When | The lead | The workers |
|---|---|---|
| 0-5 min | tools, brief (with `minutes`), start the whole team at once | claim with `wait=50` |
| every 10-15 min | `project()`: `flow`, `needs_you` `[lead]` items, silent workers, merges; between checks claim `kind="any"` packages yourself - never `sleep` and never wait idle | claim -> work -> done -> claim; journal blockers |
| ~10 min before the end | `tasks(action="stop", note="close-out")` | finish or hand back, three-line report, end |
| the end | close out, spot check, export, retro, final report | - |

## What to do when...

| You see | Do | Why |
|---|---|---|
| a claim: `keep_waiting` | claim again with `wait=50` | the pools before you are still filling |
| a claim: `other_lanes` | take one of those, then come back | work is open elsewhere now |
| a claim: a `find` package | look for more lists (references/roles/sources.md), register them, claim research again | the lanes are empty but the pool can still grow |
| a claim: `lane_finished` or `stop` | finish or hand back what you hold, journal, report in three lines, end | nothing can refill it / the team stops |
| `done` answers `next` | do what it says - normally claim again at once | a worker that ends its turn does not come back |
| `needs_you`: a worker silent 3+ min | start it again with the same role | it ended its turn |
| `needs_you`: only N worker(s) active while lanes have work | start a worker for each lane that waits, research first | one worker for every lane read 47 of 260 organisations in 35 minutes (WorkBuddy, 0.8.3) |
| `capabilities.set_aside` names a reader or engine | nothing to do - the others carry on; say it in the report if it lasts | a browser denied the network, a gateway without the endpoint |
| `flow.possible_duplicates` | `orgs(merge="suggested", keep_apart=[ids that are two])` | until merged, their routes count twice |
| `fetch` says `list_page` | `sources(add=...)` the list; pool its entries as organisations with their addresses | a list's addresses belong to its entries, not to you |
| an address on a list belongs to one of yours | `grade(set=[{value, org_id, grade, person, role}])` | it is attached from the page that prints it |
| `website_check` on a summary | make sure the site is its own; `orgs(set=[{org_id, website: ""}])` clears a wrong one | directories, portals and namesakes look like websites |
| a source paused by its pilot | leave it, or `sources(set=[{source, status: "scale"}])` if you know better | nobody waits on it |
| `time_left_min` about 10 | `tasks(action="stop")`, then close out | the end is part of the run |
| a question only the user can answer | write it down for the final report; keep working | one numbered list, once |

## Running the job

1. **Tools.** `project(name)["capabilities"]` says what is set up. Every provider is optional;
   the plugin steps down, never stops. Not installed: `install.sh` (Windows `Install.ps1`).
2. **Brief.** `project(name, brief={goal, country, language, region, scenario, grades, exclude,
   minutes, ...})`. `country` and `language` steer every engine (without them it answers as US
   English); `minutes` is the time box (smoke 15-35; a job, what the user gives). Ask the user
   once, up front, what is missing (marketops-knowledge, "Settle before starting"); after that
   never stop to ask. The brief merges: send only the keys you change.
3. **Start the whole team at once** with your host's multi-agent mode. Default: finders 1-2,
   miners 1-3, researchers 6-10, websites 0-1, finish 1-2 (a smoke: sources, pool, research - one
   each). Start line: `You are the <role> worker on MarketOps project "<project>". Call
   guide("worker") and guide("role:<role>") of the marketops tools and work until a claim says
   lane_finished or stop - after every done, claim again; never end your turn on an empty claim.`
   (Claude Code: `marketops-worker` subagents in the background; Codex / WorkBuddy: their
   multi-agent mode.) **A host with one agent** runs the same pipeline: you claim
   `tasks(action="claim", kind="any", wait=50)` in a loop - an unmined source first, then
   research, websites, finding, review - and steer between packages. Slower, never different.
4. **Lanes feed themselves.** `source`, `site`, `sweep` and `dig` refill from the pools when
   claimed. Add `find` lanes for gaps (`tasks(action="add", kind="find", title="find: <layer>,
   <region>")`), `dig` for a second look, `review` for rows to check. Order the miners
   by what makes A/B: lists that name people first; a list with a registry number (RNA, SIRET,
   UAI...) early beside them - one miner - so later lists merge on the number, never ahead of them.
5. **Steer every 10-15 minutes** from `project(name)`: move agents to where work waits between
   pools (sources nobody mines: another miner; organisations arriving faster than read: more
   researchers; pool saturated: finders become researchers). Each round, do your own work too:
   merge duplicates, answer what workers raised, restart silent workers. A lead who only watches
   leaves these to the workers (074: they waited, then decided alone).
6. **Close out yourself.** Every `needs_you` item marked `[lead]` is yours, done before you
   report: merge the suggested duplicates (say why a group stays apart); accept, correct or
   downgrade each A/B in review (say why); clear held rows (`grade(held=[{org_id, held: "X"}])`);
   put empty or out-of-scope records out (`orgs(set=[{org_id, scope: "out"}])`). Only `[user]`
   items (the brief, money, a key, a wider scope) go to the user. While 交付状态 says 不得交付
   and a `[lead]` item is open, you are not at the report yet: do the item, `export` again, read
   交付状态 again. Your own close-out never appears in the report as a "gap" or a "to do".
7. **Deliver** when the lanes are finished or stopped, duplicates merged, held rows cleared and
   the spot check done: with no finish worker, open 20 of the 抽检 rows' pages yourself and
   record each (`grade(set=[{value, org_id, checked: "ok"|"wrong"}])`). The workbook's first
   line, 交付状态, says whether it may go out (`export()["delivery"]`). `export(project)`, then
   the `learn` command. Compare on "Bruce 同口径" (A+B over organisations with any A-D route;
   his 71/249 = 28.5%) and "已研究机构".
8. **The final report is a report, not a to-do list** (078 ended on seven questions, five of
   them the lead's own work). Short, in this order:
   1. **Result**: 交付状态, organisations, A/B routes and organisations with A/B, the
      Bruce-comparable line, coverage, spot check, the workbook's path.
   2. **What was done**: sources (pooled / paused / dropped and why), team, minutes, spend.
   3. **Your calls**: the close-out judgements and why.
   4. **Known gaps**: what the run did not reach and why.
   5. **For the user** (only if any): at most two `[user]` items, each saying what it would
      change - the delivery stands without them.

## Keep the record

Every run is also a test of the plugin. The tools log every call to `calls.jsonl`; pass
`agent="<your name>"` on every call that takes it so the log can tell agents apart. Write what
the log cannot see the moment it happens: `project(name, journal={"type": "blocker" | "smooth" |
"decision" | "idea", "text": "...", "evidence": "file / count"}, agent="<name>")` - a worker at
least its blockers, the lead its decisions. Before `export`, the lead writes one `{"type":
"retro"}` entry; `export` builds `RETRO.md` from it all. A test run is then archived
(`references/benchmarks.md`, "Archive").

## Rules

- Every address comes from a page read with the tools (`fetch`, `harvest`, `run`) and keeps its
  page and quote. Never guess one. The host's own web tools are for looking only.
- One claim, one writer. Feed the pools behind you: a new list -> `sources(add)`, a venue's
  real operator -> `orgs(add)`.
- Judge by what is on disk (rows, pages, the workbook), never by a status line.
- Nothing useful is dropped: review and X are labels. Research only; nothing is sent.
- Never tune the work to one test's names or pages: what you learn goes to the playbook and the
  journal as a pattern.
