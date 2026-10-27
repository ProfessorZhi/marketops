---
name: marketops-research
description: Lead a MarketOps sales-research job with a team of agents - sources, pool, websites, digging for people, review and delivery all running at once - using your host's own multi-agent mode (Claude Code subagents, Codex agents, WorkBuddy multi-agent) and the marketops MCP tools, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "every indoor sports venue in Paris, the people who run them").
---

# MarketOps research: leading the work

You lead the job. The plugin gives you tools (the marketops MCP server and the providers
behind it), shared state on disk (pools, contacts, the task queue, the playbook) and safety
(evidence rules, one writer per organisation, a shared pace per provider). **The work is done
by agents - you and the workers you start with your host's own multi-agent mode.** The
plugin's scripts are your team's power tools: they read, extract, search and grade in bulk,
fast and the same way every time; agents decide where to look, whether a contact is right, and
what to try next.

Quality comes first: every delivered row must survive someone opening its page. Agent time is
well spent wherever it raises quality.

Knowledge to read when you need it:
- **marketops-brief** - who counts as a customer, grades, the market's job titles, delivery rules.
- **marketops-sources** - which sources and page types pay, in what order, when to stop a source.
- **marketops-tools** - how each provider and tool is best used (`references/` per provider).
- **marketops-benchmarks** - the standing tests and how to measure a run.
- `references/projects/<market>.md` - what Bruce's six lines and our runs taught for that
  market; `references/roles/*.md` - the job of each stage's workers;
  `references/bruce-prompts.md`, `references/outreach.md`.

## The chain: five stages, all running at once

```
 1 sources      2 pool             3 websites        4 dig for people        5 finish
 find lists ─▶  lists to venues ─▶ each org's site ─▶ contacts, graded,  ─▶  review, spot
                and organisations  confirmed          agent-judged            check, deliver
   ▼               ▼                  ▼                   ▼                       ▼
 pool: tasks    venues.csv          orgs.csv            contacts/ verdicts.csv  workbook
 sources.csv    orgs.csv            (+ script sweep)    playbook.json
```

Each stage takes from the one before as soon as something is there: while the source workers
look for the tenth list, dig workers are already on organisations from the first one. Nothing
waits for a stage to be complete.

| Stage | Agents decide | Scripts do (tools) | Knowledge |
|---|---|---|---|
| 1 sources | which lists exist and are worth it; check 20 rows of each | `search`, `fetch` downloads exports | marketops-sources, `references/projects/` |
| 2 pool | field mapping, what to filter out, which organisation runs a venue, unclear merges | `pool(...)` merges on name + place | marketops-sources, marketops-brief |
| 3 websites | is this site really theirs; their own page on a city portal; a federation or social page when there is no site | the sweep finds sites by name + city, checks DNS, scopes portals | marketops-tools |
| 4 dig | why an organisation has no route yet, where else to look, whether a contact belongs to it, what works for this kind of organisation | the sweep reads high-value pages (more while they bring new addresses), extracts, Jev grades; `run(orgs=[...], again=True)` re-sweeps a batch with the playbook | marketops-brief (grades, titles), marketops-sources (page types), `references/projects/` |
| 5 finish | rows to review, cross-organisation addresses, the 100-row spot check, what to put to the user | rules, dedupe, reconciliation, `export` | marketops-brief, marketops-benchmarks |

## How you run a job

1. **Brief.** Read `references/projects/` for the market, write the brief with
   `project(name, brief={...})` (marketops-brief). Ask the user once, up front, for what is
   missing; then do not stop to ask again until delivery - everything else goes into the
   numbered list (step 6).
2. **Start the sweep.** `run(project, follow=True, shards=4)`. It sweeps every organisation as
   soon as it is pooled - its site, high-value pages, PDFs, grading, a few dig rounds - in the
   background. It is the team's first pass, not the end of the work.
3. **Start the team with your host's multi-agent mode.** One worker per role, each told its
   project and role; they read `references/roles/<role>.md` and work until their queue is empty.

   | Host | How to start workers |
   |---|---|
   | Claude Code | start `marketops-worker` subagents in parallel (one per role/slot), in the background, each with `project` and `role` in its prompt; use agent teams or workflows if your setup has them |
   | Codex | its own sub-agent / multi-agent feature when available; otherwise give the user one start line per worker for extra sessions (`codex exec "<start line>"`) |
   | WorkBuddy | its multi-agent mode; otherwise one thread per worker with the start line |
   | none of these | work the stages yourself in turns, most valuable queue first |

   Start line: `You are the <role> worker on MarketOps project "<project>". Read
   references/roles/<role>.md of the marketops plugin and work until your queue is empty.`

   Default team (change it in the brief as `team`): sources 2, pool 1, websites 1, dig 4-8,
   finish 1-2. More dig workers raise quality fastest once the sweep is ahead of them.
4. **Feed the queues.** `tasks(action="plan", kind="dig")` hands out organisations the sweep
   has finished without a confirmed A/B; `kind="review"` the rows to review; sources go in as
   `tasks(action="add", title="pool: <source> <url>")`. Plan again whenever `stages` shows
   new work.
5. **Steer every 10-15 minutes, not more often.** `project(name)` -> `stages`: where work piles
   up, move workers there (sources exhausted -> those workers dig). Read the playbook
   (`playbook_segments`, `orgs(...).playbook`): when dig workers have written how a kind of
   organisation is found, re-sweep the rest of that segment -
   `run(project, orgs=[ids of that segment not yet done], again=True)`. Check source yields
   (a source under 5% A/B after 30 organisations goes to the back).
6. **One numbered list for the user.** Collect what only the user can decide in
   `project()["needs_you"]` plus anything your workers raised; put it to the user once, in one
   message, and act on the reply.
7. **Finish.** `run(project, pool_done=True)` when the sources are exhausted; when the queues
   are empty, the finish workers do the spot check (100 confirmed A/B rows on the 抽检 sheet,
   each page opened, `grade(set=[{value, org_id, checked}])`). Deliver when precision is 90% or
   better and the 对账 line adds up: `export(project)`. Then run the `learn` command so the
   next project starts from what this one taught.

## Deciding what agents do and what scripts do

- Repeatable, the same for every organisation, high volume: a script tool (`run`, `pool`,
  `fetch`, `grade`).
- One closed question per row, many rows: a model call through the tools (`grade`, `judge`).
- Finding something out, reasoning about who is who, changing approach: an agent.
- What an agent learns for one kind of organisation goes into the playbook
  (`project(name, playbook={segment: {paths, queries, roles, notes}})`), and the scripts apply it
  to the whole segment. That is how a sample studied by agents lifts thousands of organisations.

## Measure, don't guess

- The workbook: 说明 (breadth, routes, precision, 对账), 机构, 联系人, 抽检, agent 结论,
  数据源, 贡献（消融）, 过程（耗时）, 成本.
- Breadth (share of organisations with a confirmed A/B) first, then A+B routes per 100
  organisations, precision, cost (provider spend from the tools; agent tokens from your host,
  reported apart), and what each source, stage and agent pass added.
- Ablation: `brief.ablate = ["dig", "evidence", "reverify", "rank", "discover", "findsite"]`
  switches a script stage off on the same project (pages come from the library, almost free).

## What went wrong for Bruce, and how this avoids it

- **Waiting and redoing ate his time, not crawling**: stages run at once; everything is on disk
  and resumes; a dead process or worker is replaced (a stale claim returns to the queue).
- **Green but empty**: judge by what is on disk - contacts, pages, the workbook - never a
  "completed" line.
- **Silent drops**: every organisation ends with contacts or an agent verdict saying why not.
- **Human review he could not finish**: workers review first; only what they cannot decide
  reaches the user, in one list.
- **Endless pools**: measure each source after 30 organisations; move on from the weak ones.
- **Rules relearned by every thread**: the brief, the playbook and `references/projects/` (via
  `learn`) carry them to the next worker and the next project.

## Rules that stay

- Never guess an email; every contact comes from a page read with `fetch` (or the sweep) and
  keeps its quote and page. A host's own web fetch is for looking, not for adding rows.
- Research only: nothing is sent to anyone. The output is called 研究有效邮箱.
- Nothing useful is dropped: weak evidence, review and X are labels; a person or agent decides.
- Spending: every provider call is logged; each paid account keeps a red line at 10% of its
  first balance; low balances appear in `needs_you`.
