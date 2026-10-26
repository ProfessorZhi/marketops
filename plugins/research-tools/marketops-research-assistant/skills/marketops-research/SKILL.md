---
name: marketops-research
description: Market research for sales with the marketops tools - keep finding data sources, maintain the venue and organisation pools, read their pages and grade the contacts found, all at once, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "every indoor sports venue in Paris, the people who run them").
---

# MarketOps research

You do the research; the tools give you reach. One project folder (`~/MarketOps/<project>`),
plain files you can open. Keep going until the job is done; do not stop to ask.

## The work is five pools, filled at the same time

```
data sources ──> venues + organisations ──> pages ──> contact candidates ──> Excel
 (sources.csv)    (venues.csv, orgs.csv)    (library)   (contacts/, graded,
                                                         with evidence)
```

Nothing waits for the previous pool to be complete: while contacts are found for the
organisations already pooled, new sources keep adding organisations, and they join the flow.
Every pool is a union - each new source adds to it, the same venue or organisation from two
sources becomes one row, nothing is dropped.

| Tool | Use it for |
|---|---|
| `project(name, brief?)` | open/create, save the brief, see every pool, the runs, the queue, costs, balances |
| `search(query, maps?)` | Serper + SerpApi + DeepSeek + TinyFish at once; `maps=True` for places |
| `pool(project, file, filters, map, group_by)` | add a saved data file (open data, a club list, your own CSV) to the pools |
| `fetch(project, url, org_id?)` | read one page with all readers at once; data files are downloaded |
| `run(project, shards?, follow?)` | the pipeline in background processes (below) |
| `tasks(project, action, ...)` | the work queue for parallel worker agents |
| `browse(project, url)` | TinyFish Agent for pages that need clicks - paid, only when nothing else can read it |
| `judge(project, file, question, options, column)` | Jev answers one multiple-choice question per row |
| `grade(project, set?)` | Jev grades contacts; `set=[...]` for your own grades, names, roles |
| `export(project)` | the workbook and Bruce's 18-column routes CSV |

## How to run a job

1. **Brief** (ask at most once for what is missing): `project(name, brief={...})`
   - `goal`, `region`, `exclude`; `grades`: what A/B/C/D/X mean, with the market's own job
     titles (omitted grades use Bruce's defaults in the playbook).
   - `focus.find`: what to look for ("gymnase", "salle d'escalade", "club de badminton"...);
     `focus.areas`: where (the city's districts, the region's towns). The run's discovery
     line searches every find x area pair (maps, else web results; directory pages) and
     adds the organisations it finds. Add more words any time; new pairs are picked up.
   - `focus.search_terms`: the market's words for the people wanted ("responsable
     équipement sportif", "Platzwart"); `focus.directory_terms`: the lists that name them.
2. **Known sources first**: the big registers and open-data exports - `fetch` the export,
   then `pool(project, "pages/<file>", filters=..., map=..., group_by=<operator column>)`.
   A big city has far more organisations than one register: federation club finders, the
   city's association directory, schools and universities, private chains - each a `pool`.
   Never cut a list to a first N unless the user asks.
3. **Start the pipeline at once**: `run(project, follow=True, shards=4)`. Every organisation
   flows on its own, dozens at a time: its site is read (best contact, team, board, venue
   pages and PDFs, two levels deep), new addresses are graded, then while it has no
   confirmed A/B it is dug a different way each round (your search terms, its site and
   PDFs, directories, maps, broad searches), and its A/B evidence is corroborated.
   Organisations clearly outside the brief (marked `scope=out` by Jev) stay in the pool
   but are skipped.
4. **Workers in parallel**: start 3-6 `marketops-worker` subagents with the project name.
   Give them sources to pool (`tasks(action="add", title="pool: <source>")`) and, as the run
   goes, `tasks(action="plan", kind="dig" | "review" | "evidence" | "browse")`.
5. **When the pool is complete**: `run(project, pool_done=True)`; the run finishes and exports.
   `project(name)` shows progress (organisations per minute, minutes left, cost per
   organisation) any time; `run(project, stop=True)` stops gracefully.
6. **Review and deliver**: rows marked `review` (with their reason) and weak evidence are
   kept and delivered, marked; look at the A/B ones, confirm or correct with `grade(set=...)`.
   `export` - report breadth (share of organisations with A+B) first, then A+B routes per
   100 organisations, coverage, cost, and what the sources and stages contributed.

## What to keep, what to drop: measure it

The workbook's "数据源" sheet shows what each source brought (organisations, A+B yield) and
"贡献（消融）" what each stage (site, dig rounds, evidence) and reader added that nothing
else found. A source with near-zero yield after ~30 organisations: stop adding from it and
look for the next. To test a stage, switch it off and run again on the same project (pages
and searches come from the library, so it costs almost nothing):
`brief.ablate = ["dig", "evidence", "reverify", "rank", "discover"]` (any of them).

## Nothing starts from zero

Pages and search answers live in one library shared by every project and plugin version
(`~/.marketops/library`, or `MARKETOPS_LIBRARY`); pages read in the last 30 days and searches
in the last 14 are reused for free; older A/B rows are checked again on their page.

## Rules that stay

- Never guess an email; every contact comes from a page and keeps its quote and page.
- Research only: nothing is sent to anyone.
- Every organisation ends with contacts or the reason none were found.
- Spending: every call is logged; each paid account keeps a red line at 10% of its first
  balance. Providers' shares are in `brief.providers`; DeepSeek, TinyFish search/fetch,
  Crawl4AI and Jev are used freely, Serper and SerpApi by share, the TinyFish Agent only
  when asked.

Bruce's definitions, grades and the methods that paid off: [the playbook](../../references/PLAYBOOK.md).
