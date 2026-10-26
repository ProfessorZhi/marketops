---
name: marketops-research
description: Market research for sales with the marketops tools - keep finding data sources, maintain the venue and organisation pools, read their pages and grade the contacts found, all at once, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "every indoor sports venue in Paris, the people who run them").
---

# MarketOps research

You do the research; the tools give you reach. One project folder (`~/MarketOps/<project>`),
plain files you can open. Keep going until the job is done; do not stop to ask.

Before you start, read the other skills when you need them:
- **marketops-brief**: write the brief - customer, grades, the market's job titles, delivery rules.
- **marketops-sources**: which sources and pages to use first, and when to stop a source.
- **marketops-tools**: how to phrase searches and get each provider to work.
- **marketops-benchmarks**: the two standing tests and how to find what slows or loses results.
- `references/projects/`: what each market and line taught in earlier projects (Bruce's and
  ours); read the matching file first, and run the `learn` command when a project ends.

## Five pools, filled at the same time

```
data sources ──> venues + organisations ──> pages ──> contact candidates ──> Excel
 (sources.csv)    (venues.csv, orgs.csv)    (library)   (contacts/, graded,
                                                         with evidence)
```

Nothing waits for the previous pool to be complete: while contacts are found for the
organisations already pooled, new sources keep adding organisations and they join the flow.
Every pool is a union - the same venue or organisation from two sources is one row.

| Tool | Use it for |
|---|---|
| `project(name, brief?)` | open/create, save the brief, see pools, runs, queue, costs, balances |
| `search(query, maps?)` | all search engines at once; `maps=True` for places |
| `pool(project, file, filters, map, group_by)` | add a saved data file to the pools |
| `fetch(project, url, org_id?)` | read one page with all readers at once; data files are downloaded |
| `run(project, shards?, follow?)` | the pipeline in background processes |
| `tasks(project, action, ...)` | the work queue for parallel `marketops-worker` agents |
| `browse(project, url)` | TinyFish Agent for pages that need clicks - paid, last resort |
| `judge(project, file, question, options, column)` | Jev answers one multiple-choice question per row |
| `grade(project, set?)` | Jev grades contacts; `set=[...]` for your own grades, names, roles |
| `export(project)` | the workbook and Bruce's 18-column routes CSV |

## How to run a job

1. **Brief** (ask at most once for what is missing): `project(name, brief={...})` - see
   marketops-brief.
2. **Known sources first**: the market's registers and open-data exports (marketops-sources):
   `fetch` the export, `pool(project, "pages/<file>", ...)`. A big city has many more
   organisations than one register - each federation club finder, association directory,
   school list and private chain is one more `pool`. Never cut a list to a first N.
3. **Start the pipeline at once**: `run(project, follow=True, shards=4)`. Each organisation
   flows on its own:
   - no website: its own site is looked up once (name + town);
   - site: its contact, team, board and venue pages and PDFs, two levels deep (on a portal
     shared by many organisations, only its own page and below);
   - graded in the background (each address with the same text once);
   - while it has no confirmed A/B, a different dig each round: your search terms, its site
     and PDFs, directories and federation pages, maps, broad searches;
   - its A/B evidence corroborated.
   Sources take turns, so a source pooled late is not stuck at the back; after 30 organisations
   a source with >=10% A/B gets two turns, one under 5% waits until the others are empty
   (`project()` shows each source's yield). With `focus.find` and `focus.areas` a discovery
   line keeps adding organisations while this runs.
4. **Workers in parallel**: start 3-6 `marketops-worker` subagents with the project name;
   give them sources to pool (`tasks(action="add", title="pool: <source>")`) and, as the run
   goes, `tasks(action="plan", kind="dig" | "review" | "evidence" | "browse")`.
5. **Pool complete**: `run(project, pool_done=True)`; the run finishes and exports.
   `project(name)` shows progress any time; `run(project, stop=True)` stops gracefully.
6. **Review and deliver**: rows marked `review` (with their reason) and weak evidence are
   kept and delivered, marked; check the A/B ones, confirm or correct with `grade(set=...)`.
   Report breadth (share of organisations with A+B) first, then A+B routes per 100
   organisations, coverage, cost, and what sources and stages contributed.

## Measure, don't guess

The workbook shows where time and yield come from:
- "过程（耗时）": seconds per organisation per stage and source (from `trace.jsonl`, one
  line per organisation) and the stage where each organisation's first A/B appeared;
- "数据源": what each source brought; "贡献（消融）": what each stage and reader added that
  nothing else found.
A source with near-zero yield after ~30 organisations: stop adding from it, find the next.
To test a stage, switch it off and rerun on the same project (pages and searches come from
the library, almost free): `brief.ablate = ["dig", "evidence", "reverify", "rank",
"discover", "findsite"]` (any of them).

## What went wrong for Bruce, and how this avoids it

- **Green but empty**: status fields said done while nothing was on disk. Judge by what is on
  disk: contacts, pages, the workbook - never by a "completed" line.
- **Silent drops**: a stage produced results that never reached the delivery table (953 real
  municipal addresses in one case). Every organisation must end with contacts or a reason;
  when the pool is large and the output tiny, find the stage whose output was not merged.
- **Endless pools**: 128,685 organisations, nothing sendable. Measure each source and move on.
- **Progress in pages**: 220 pages gave 5 routes. Report organisations with A/B and routes,
  and attempts next to pages.
- **Heartbeats instead of work**: polling every few minutes added 8 pages in 14 minutes. Start
  the run, let it work, look at `project()` when there is something to decide.
- **Definitions settled late**: most of Bruce's interventions were definitions. Settle them in
  the brief; a correction updates the brief and the project's knowledge file.

## Nothing starts from zero

Pages and search answers live in one library shared by every project and plugin version
(`~/.marketops/library`, or `MARKETOPS_LIBRARY`): pages from the last 30 days and searches
from the last 14 are reused for free; older A/B rows are checked again on their page.

## Rules that stay

- Never guess an email; every contact comes from a page and keeps its quote and page.
- Research only: nothing is sent to anyone.
- Every organisation ends with contacts or the reason none were found.
- Nothing useful is dropped: weak evidence and review are labels, a person decides.
- Spending: every call is logged; each paid account keeps a red line at 10% of its first
  balance. Shares in `brief.providers`; DeepSeek, TinyFish search/fetch, Crawl4AI and Jev
  freely, Serper and SerpApi by share, the TinyFish Agent only when asked.
