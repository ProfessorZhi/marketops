---
name: marketops-research
description: Market research for sales with the marketops tools - from a public dataset to a venue pool, an organisation pool and graded contacts with evidence, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "Swiss natural-grass football pitches, find the people who look after the grounds").
---

# MarketOps research

You do the research; the tools give you reach. Eleven tools, one project folder
(`~/MarketOps/<project>`), plain CSV files you can read and edit with your own tools.
Keep going until the job is done; do not stop to ask whether to continue.

| Tool | Use it for |
|---|---|
| `project(name, brief?)` | create/open the project, save the brief, see pools, contacts by grade, jobs, costs, balances |
| `search(query, maps?)` | Serper + SerpApi + DeepSeek + TinyFish at once; `maps=True` for places with website and phone |
| `pool(project, file, filters, map, group_by)` | build `venues.csv` / `orgs.csv` from a saved open-data file; one organisation per operator |
| `fetch(project, url, org_id?, org?)` | read a page with Crawl4AI + TinyFish + DeepSeek at once; data exports download as files |
| `crawl(project, "orgs.csv")` | background: each organisation's site, home + contact/imprint/team/board pages |
| `browse(project, url, goal?)` | background TinyFish Agent for pages that need clicks (paid per step) |
| `judge(project, file, question, options, column)` | Jev answers one multiple-choice question per row |
| `grade(project, set?)` | Jev grades contacts A/B/C/D/X from the brief; `set=[...]` for your own grades, names and roles |
| `run(project, shards?, follow?, rounds?, goal_ab_org_share?, agent?, stop?, pool_done?)` | the pipeline, as background process(es): every organisation flows site -> graded -> dug while it lacks A/B -> evidence corroborated; `follow=True` takes organisations added while it runs |
| `tasks(project, action, ...)` | shared work queue: plan packages (dig, review, browse, custom), workers claim and finish them |
| `export(project)` | the Excel workbook and Bruce's 18-column routes CSV |

## The flow

1. **Brief.** Ask at most once, up front, for anything missing: what the user sells, the
   region, the kind of venue/organisation, the roles that matter. Save it with
   `project(name, brief={goal, region, exclude, grades, focus})`. `focus` steers the long
   run: `{"search_terms": [the market's words for the people wanted, e.g. "Vorstand
   Kontakt", "Platzwart", "responsable équipements sportifs"], "directory_terms": [the
   lists that name them, e.g. "Fussballverband Region Zürich Verein", "annuaire clubs
   district"]}`. Put `priority` (high/normal/low) in `orgs.csv` to set what goes first
   (`judge` can fill it). A big city has far more people to reach than one list shows: add
   every family tied to the venues - the operators, the clubs and associations that play
   there (federation and league club finders, the city's association directory), schools
   and universities, private operators and chains - each as rows in `orgs.csv`. `grades` describes A/B/C/D/X in
   plain words with the market's own job titles, in its own language (Platzwart, Leiter
   Sportamt, head groundsman ...). Omitted grades use Bruce's defaults (see the playbook).
   Broad first: when unsure, keep a row and tag it; narrowing comes later.

2. **Venue pool (`venues.csv`).** The whole area, not a sample: never cut the list to a
   first N unless the user asks. Find the complete list before visiting sites one by one:
   open-data portals (Opendatasoft `/exports/json`, CKAN, Socrata, ArcGIS), federation and
   association club lists, national registers, `search(..., maps=True)` per district. `fetch`
   a data URL saves the file in `pages/`; `pool(project, "pages/<file>", filters=...,
   map=..., group_by=<operator column>)` turns it into `venues.csv` and `orgs.csv` (it
   returns the file's columns so you can map them). **The pools are the union of every
   source**: call `pool` for each one - the open-data register, each federation's club list,
   maps results, the city's association directory, lists you write yourself (save them as
   CSV in `pages/`, `into="orgs"` for organisations). The same venue or organisation from two
   sources becomes one row with both sources and the fields of both. Names too different to
   match: `judge(project, "orgs.csv", "Is this the same organisation as ...?", ...)`. Use
   `judge` for in/out of scope at volume instead of dropping rows.

3. **Organisation pool (`orgs.csv`, columns `id,name,website,...`).** Who runs each venue:
   the municipality's sports/property department for public venues, the club, school,
   trust or company otherwise. One organisation often runs many venues: one row, all its
   venues linked by `org_id` in `venues.csv`. Look at every family tied to the venues:
   operators/owners, clubs playing there, schools, the federation's club list. Find missing
   websites with `search`; `judge` can check "is this URL the organisation's own site?".

4. **Contacts: the pipeline.** Start it as soon as the first organisations are in
   `orgs.csv` - don't wait for the pool to be complete: `run(project, follow=True,
   shards=4)`. Each organisation flows on its own, dozens at a time: its site is read (home
   plus the best contact, imprint, team, board, staff and venue pages two levels deep, PDFs
   included), a background grader grades the new addresses, then while it has no confirmed
   A/B it is dug a different way each round (your search_terms, inside the site and its
   PDFs, directories, maps, broad searches), and the evidence for its A/B addresses is
   corroborated (pages that also print the address). Keep adding organisations; they join
   the flow. When the pool is complete: `run(project, pool_done=True)`. It runs as its own process and keeps going if this
   session ends; `project(name).run.progress` shows organisations per minute, minutes left
   and cost per organisation, so you can tell the user what the rest will take; `run(name,
   stop=True)` ends it gracefully. Check `project(name).run` now and then and keep working
   on the pools meanwhile: more sources, more organisations, websites for those without one
   (append them to `orgs.csv` and call `run` again - finished work is never repeated, saved
   pages are never fetched twice). Many batches or thousands of organisations: put a `batch`
   column in `orgs.csv`; for runs longer than this session use the `headless` command `run`
   returns. Single pages: `fetch` with `org_id`; pages that need clicks: `browse`.
   A federation or association page listing many clubs' officials is gold: `fetch` it and
   assign its contacts to the right organisations.

5. **Review.** Jev grades every address from its quote and fills `person`/`role` on A/B rows
   from the quote's own segments. Every address also gets an evidence score (0-100: same
   card, own site, own domain, named, role stated, several readers, several pages, Jev's
   confidence; minus when several organisations list it) - strong, medium or weak. Weak rows
   are never dropped: they are delivered marked weak, after the strong ones; the person
   decides. `tasks(plan, kind="evidence")` sends workers to find more proof for them. Rows marked `review=yes` carry a `review_reason` (low
   confidence, address listed for several organisations, page on another site) and do not
   count until you look: open the quote, then confirm or correct with
   `grade(set=[{value, org_id, grade, person, role}])`. Your grades are never overwritten.

6. **Deliver.** `export(project)` writes the workbook (summary, per-batch table, contacts A
   first with page, quote and review flag, organisations with best grade and crawl status,
   venues, costs, X rows apart) and Bruce's 18-column routes CSV. Report the summary
   numbers by their exact names: breadth (share of organisations with A+B) first, then A+B
   routes per 100 organisations (also capped at 3 per organisation), coverage (A-D), still
   to review, cost.

Keep going. A first pass is not the job: when `run` stops, look at what is still missing
(organisations without A/B, without a website, unreadable sites, email buttons without
addresses) and attack it - new sources, `browse`, better focus terms - then `run` again.

## Many agents at once

A whole city or region is a lot of work; split it. You are the coordinator:

1. Brief and the first pools yourself. Big source lists split well: `tasks(project,
   action="add", title="pool: <source / district / federation list>")` per source.
2. Start the machine work: `run(project, shards=4)` (four processes, `orgs.csv` split
   between them; each reads ~12 organisations at a time with several providers in parallel).
3. Start workers in parallel - the `marketops-worker` subagent, 3 to 6 at once, each told
   the project name. They claim packages from the queue, do them with the tools and hand them
   back until it is empty.
4. When a run part reaches its dig rounds, or after it ends: `tasks(project, action="plan",
   kind="dig")` (organisations still without A/B), `kind="review"` (A/B rows to check),
   `kind="browse"` (hidden email buttons) - and send workers again.
5. `project(name)` shows the runs and the queue; `export` when the queue and the runs are done.

Workers and run processes write the same project safely (files are locked). Speed comes from
parallel work; quality stays: every address is kept with its quote, nothing is dropped.

## Provider weights

`brief.providers` sets how often each provider takes part. Defaults:
`{"search": {"deepseek": 1, "tinyfish": 1, "serper": 0.5, "serpapi": 0.3},
  "fetch": {"crawl4ai": 1, "tinyfish": 1, "deepseek": 1}}` - DeepSeek, TinyFish search and
fetch, Crawl4AI and Jev are cheap: use them freely. Serper and SerpApi cost per search: a
share. The TinyFish Agent (`browse`, `run(agent=True)`) is paid per step: use it only for
pages nothing else can read or whose addresses hide behind buttons, and say so to the user.
Held-back providers are still called when the chosen ones come back thin (few search results,
a page nobody could read, a contact page with no address), so weights save cost without
losing results. Crawl4AI always reads (it keeps the page structure that binds names to
addresses). Jev ranks deep-dig leads (best first, none dropped) and grades every address.

## Nothing starts from zero

Pages and search answers live in one library shared by every project and every plugin
version (`~/.marketops/library`, or `MARKETOPS_LIBRARY` for a team folder). A page read in
the last 30 days and a search asked in the last 14 are reused for free; older A/B rows are
checked again on their page (gone -> marked, not dropped). Open the previous project for the
same area first and continue it, or start a new one: either way nothing already read is paid
for twice.

## Rules that stay

- Never guess or construct an email; every contact row comes from a page and keeps its quote.
- Research only: nothing is sent to anyone.
- Every organisation gets a row in the end, including "not found" and why.
- Spending: every call is logged; each paid account has a red line at 10% of its starting
  balance and is skipped below it while the others carry on. No other caps. Report cost.

Read [the playbook](../../references/PLAYBOOK.md) for Bruce's definitions, grades and the
methods that paid off in past runs.
