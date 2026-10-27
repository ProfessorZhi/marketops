---
name: marketops-research
description: Lead a MarketOps sales-research job as a super-pipeline of pools (sources -> pages -> venues -> organisations -> contacts) worked by a team of agents - finding lists, mining them, finding websites, researching people, review and delivery all running at once, several agents per stage - using your host's own multi-agent mode (Claude Code subagents, Codex agents, WorkBuddy multi-agent) and the marketops MCP tools, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "every indoor sports venue in Paris, the people who run them").
---

# MarketOps research: leading the work

You lead the job. The plugin gives you tools (the marketops MCP server and the providers
behind it), shared state on disk (pools, contacts, the task queue, the playbook) and safety
(evidence rules, one writer per organisation, a shared pace per provider). **The work is done
by agents - you and the workers you start with your host's own multi-agent mode.** The
plugin's scripts are your team's power tools, and they only act when an agent calls them: they
read, extract, search and grade in bulk, fast and the same way every time; agents decide where
to look, whether a contact is right, and what to try next. Crawl4AI, TinyFish, DeepSeek, Serper,
SerpApi and Jev are tools in the agents' hands, not a process that runs by itself.

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

## The work is a super-pipeline of pools

```
 SOURCES ──mine──▶ PAGES ──pool──▶ VENUES ──who runs it──▶ ORGANISATIONS ──read, judge──▶ CONTACTS ──▶ workbook
 finders           miners          miners                  websites, researchers          researchers, finish
    ▲                 │                                         ▲      │
    └─ a page links to another list                            └──────┴─ a venue's real operator, a contractor
```

Each arrow is a stage with its own queue (a lane in `tasks`), and **every stage runs at the
same time as every other**: while miners turn the first list into pages, venues and
organisations, finders are already looking for the next list, and researchers are reading the
organisations that arrived a minute ago. **Inside a stage several agents work at once**, each on
its own claim - one source per miner, one package of about ten organisations per researcher -
so throughput grows with the agents you put on a lane, and no two writers ever touch the same
thing. The workers are agents; the plugin gives them the power tools, the queues and the
one-writer guarantee. Arrows also run backwards: a page that links to another directory is a
new source (`sources(add=...)`), a venue's real operator is a new organisation (`orgs(add=...)`).

| Pool | File | Fed by (lane) | Agents decide | Power tools |
|---|---|---|---|---|
| sources | `source_pool.csv` (state), `sources.csv` (what each brought) | finders (`find` lanes you add) | which lists exist, what one row really is (20 rows), worth mining or not | `search`, `fetch`, `sources(add / set)` |
| pages | `frontier.csv`, `pages/harvest-*.csv` | miners (`source` lane, one source per claim) | the entry and paging pattern, whether 20 harvested rows are right, the field mapping | `harvest` (paging, sitemap, bulk reading, one row per entry page, Jev picks the site) |
| venues | `venues.csv` | miners | who runs each venue (owner, operator, contractor) | `pool(group_by=...)`, `judge` |
| organisations | `orgs.csv` | miners, researchers' `orgs(add)` | the same organisation or not, its own website | `pool`, `orgs(set / add)`, `run(orgs, rounds=0)` finds sites (`site` lane for the rest) |
| contacts | `contacts/`, `verdicts.csv` | researchers (`sweep` lane), finish (`review`) | right person, right organisation, right grade; where else to look | `run(orgs, rounds=0)`, `fetch`, `grade` (Jev), `grade(set)` |

`project(name)["flow"]` shows every pool's size, **what waits between pools**
(`waiting_between_pools`), every lane's open and claimed packages and the agents on them, and
whether the organisation pool adds up (`reconcile`: every organisation came from a logged
source). Staff the lane where work waits. Lanes refill themselves: a worker that claims an
empty `sweep`, `source` or `site` lane gets a package cut on the spot from what has arrived.

Why pools, from Bruce's sessions (`marketops-session/docs/FINAL-REPORT.md`): the only shape that
carried throughput was "upstream materialises, the middle consumes, downstream verifies" - one
refactor consumed 33 channels in one go where the "everyone fetches" version stalled every
batch. His worst losses were a middle layer never used up: 18,954 rows built and 2 written
back; 2,733 pairs and 582 delivered (953 real council addresses dropped); 343 new German rows
never merged - so every pool here shows what waits in it, and `needs_you` names a harvested
file that never reached the pools. His one fully trusted pool grew by exactly the sum of its
packages' new rows - the `reconcile` line is that check.

## How you run a job

0. **Check the tools, install if needed.** If the marketops tools (`project`, `fetch`, ...) are
   not in your tool list, the plugin is not installed on this computer: run `install.sh` from the
   plugin folder (Windows: `Install.ps1`; it asks for the keys it does not have), then restart the
   session (`HOSTS.md` for Codex and WorkBuddy). `python scripts/server.py --check` tells what is
   there. Then `project(name)["capabilities"]`: every provider is optional and the plugin steps
   down instead of stopping -

   | Level | Means | You do |
   |---|---|---|
   | full | a Google engine (Serper or SerpApi) plus another, two or more readers, Jev | the normal flow |
   | reduced | something missing: slower search, fewer readers, or no Jev | the same flow; without Jev grade the rows the rules leave with `grade(set=[...])` (the review and finish workers share it); tell the user once which keys would bring it back to full |
   | minimal | no search engine, or plain HTTP reading only | search with your host's own web search and `fetch()` what you find; expect script-built pages to read thin; put the missing keys first in the numbered list |

   Several keys per provider can be configured (slots); a used-up key hands over to the next by
   itself, and new keys work without a restart.
1. **Brief.** Read `references/projects/` for the market, write the brief with
   `project(name, brief={...})` (marketops-brief). Ask the user once, up front, for what is
   missing; then do not stop to ask again until delivery - everything else goes into the
   numbered list (step 6).
2. **Open the lanes.** Ask the user for the lists they already have (a client register or CRM
   export is one source - one hand-given snapshot outweighed every automatic path in Bruce's UK
   runs) and register them with `sources(add=[...])`. Add one `find` lane per kind of list or
   region the brief needs (`tasks(action="add", kind="find", title="find: federation club
   finders, Île-de-France")`). The `source`, `site` and `sweep` lanes cut themselves from the
   pools when a worker claims them; `tasks(action="plan", kind=...)` does it by hand.
3. **Start the whole team at once with your host's multi-agent mode** - every stage from the
   first minute, not one after another. One worker per slot, each told its project and role;
   they read `references/roles/<role>.md` and work until their lane is empty.

   | Host | How to start workers |
   |---|---|
   | Claude Code | start `marketops-worker` subagents in parallel (one per slot), in the background, each with `project` and `role` in its prompt; use agent teams or workflows if your setup has them |
   | Codex | its own sub-agent / multi-agent feature when available; otherwise give the user one start line per worker for extra sessions (`codex exec "<start line>"`) |
   | WorkBuddy | its multi-agent mode; otherwise one thread per worker with the start line |
   | none of these | work the lanes yourself in turn: find one list, mine it, research its organisations, then the next |

   Start line: `You are the <role> worker on MarketOps project "<project>". Read
   references/roles/<role>.md of the marketops plugin and work until your queue is empty.`

   Default team (change it in the brief as `team`): **sources (finders) 1-2, pool (miners) 1-3,
   research 6-10, websites 0-1, finish 1-2.** Finders and miners start first and run beside the
   researchers for as long as the source pool is not saturated (`flow.pools.sources.saturated`:
   three or more sources, the last three each under 5% new organisations). A pool from a single
   list - or one inherited from an earlier project - is never complete; that is how a run ends
   with no venues and half a market missing. Throughput grows with the agents on each lane -
   there is no background process to wait for.
4. **Keep every lane fed.** Lanes refill from the pools; you add what only you see: `find` lanes
   for gaps (a segment, a district, a kind of list nobody tried), `kind="review"` for rows still
   to review, `kind="dig"` for organisations worth a second look (a playbook entry written later,
   a website fixed).
5. **Steer every 10-15 minutes, not more often.** `project(name)["flow"]`: where work waits
   between pools, move agents there - sources found and nobody mining them: another miner;
   organisations arriving faster than they are read: more researchers; the source pool saturated:
   finders become miners or researchers. Read the playbook (`orgs(...)["playbook"]`): when
   researchers have written how a kind of organisation is found, the packages still open for
   that segment use it automatically; for organisations already done, plan `kind="dig"`
   packages for that segment. Check source yields (a source under 5% A/B after 30 organisations
   goes to the back: `sources(set=[{source, status: "dropped", note}])` when it is not worth more).
6. **One numbered list for the user.** Collect what only the user can decide in
   `project()["needs_you"]` plus anything your workers raised; put it to the user once, in one
   message, and act on the reply.
7. **Finish.** When the queues are empty, the finish workers do the spot check (100 confirmed
   A/B rows on the 抽检 sheet, each page opened, `grade(set=[{value, org_id, checked}])`).
   Deliver when precision is 90% or better and the 对账 line adds up: `export(project)`. Then
   run the `learn` command so the next project starts from what this one taught.

**Fast mode (optional).** For a very large pool with few agents available,
`run(project, follow=True, shards=4)` sweeps every organisation in the background on its own,
with template digging; researchers then take `kind="dig"` packages of what it left. Quality is
lower - the scripts do not judge - so use it only when the pool is too big for the team.

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

- Never guess an email; every contact comes from a page read with `fetch` or `run(orgs=...)` and
  keeps its quote and page. A host's own web fetch is for looking, not for adding rows.
- Research only: nothing is sent to anyone. The output is called 研究有效邮箱.
- Nothing useful is dropped: weak evidence, review and X are labels; a person or agent decides.
- Spending: every provider call is logged; each paid account keeps a red line at 10% of its
  first balance; low balances appear in `needs_you`.
