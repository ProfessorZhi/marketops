---
name: marketops-research
description: Market research for sales with the marketops tools - from a public dataset to a venue pool, an organisation pool and graded contacts with evidence, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "Swiss natural-grass football pitches, find the people who look after the grounds").
---

# MarketOps research

You do the research; the tools give you reach. Nine tools, one project folder
(`~/MarketOps/<project>`), plain CSV files you can read and edit with your own tools.
Keep going until the job is done; do not stop to ask whether to continue.

| Tool | Use it for |
|---|---|
| `project(name, brief?)` | create/open the project, save the brief, see pools, contacts by grade, jobs, costs, balances |
| `search(query, maps?)` | Serper + SerpApi + DeepSeek + TinyFish at once; `maps=True` for places with website and phone |
| `fetch(project, url, org_id?, org?)` | read a page with Crawl4AI + TinyFish + DeepSeek at once; data exports download as files |
| `crawl(project, "orgs.csv")` | background: each organisation's site, home + contact/imprint/team/board pages |
| `browse(project, url, goal?)` | background TinyFish Agent for pages that need clicks (paid per step) |
| `judge(project, file, question, options, column)` | Jev answers one multiple-choice question per row |
| `grade(project, set?)` | Jev grades contacts A/B/C/D/X from the brief; `set=[...]` for your own grades, names and roles |
| `run(project, rounds?, goal_ab_per_100?, agent?)` | background long research over the whole `orgs.csv`: crawl, grade, dig the gaps round after round, export |
| `export(project)` | the Excel workbook and Bruce's 18-column routes CSV |

## The flow

1. **Brief.** Ask at most once, up front, for anything missing: what the user sells, the
   region, the kind of venue/organisation, the roles that matter. Save it with
   `project(name, brief={goal, region, exclude, grades, focus})`. `focus` steers the long
   run: `{"search_terms": [the market's words for the people wanted, e.g. "Vorstand
   Kontakt", "Platzwart", "responsable équipements sportifs"], "directory_terms": [the
   lists that name them, e.g. "Fussballverband Region Zürich Verein", "annuaire clubs
   district"]}`. Put `priority` (high/normal/low) in `orgs.csv` to set what goes first
   (`judge` can fill it). `grades` describes A/B/C/D/X in
   plain words with the market's own job titles, in its own language (Platzwart, Leiter
   Sportamt, head groundsman ...). Omitted grades use Bruce's defaults (see the playbook).
   Broad first: when unsure, keep a row and tag it; narrowing comes later.

2. **Venue pool (`venues.csv`).** Find the whole list before visiting sites one by one:
   open-data portals (Opendatasoft `/exports/csv`, CKAN, Socrata, ArcGIS), federation and
   association club lists, national registers, `search(..., maps=True)` per town. `fetch` a
   data URL saves the file in `pages/`; parse it with your own Python into `venues.csv`.
   Keep a `source` column. Use `judge` for in/out of scope at volume (e.g. "outdoor natural
   grass?") instead of dropping rows.

3. **Organisation pool (`orgs.csv`, columns `id,name,website,...`).** Who runs each venue:
   the municipality's sports/property department for public venues, the club, school,
   trust or company otherwise. One organisation often runs many venues: one row, all its
   venues linked by `org_id` in `venues.csv`. Look at every family tied to the venues:
   operators/owners, clubs playing there, schools, the federation's club list. Find missing
   websites with `search`; `judge` can check "is this URL the organisation's own site?".

4. **Contacts: the long run.** `run(project)` works through the whole `orgs.csv` without
   stopping: it reads every site (home plus the best contact, imprint, team, board, staff and
   venue pages two levels deep, PDFs included, all readers in parallel), grades, then takes
   the organisations still without A/B and digs a different way each round (your
   search_terms, inside the site and its PDFs, directories, maps, broad searches) until the
   goal, two dry rounds or `rounds`. Check `project(name).run` now and then and keep working
   on the pools meanwhile: more sources, more organisations, websites for those without one
   (append them to `orgs.csv` and call `run` again - finished work is never repeated, saved
   pages are never fetched twice). Many batches or thousands of organisations: put a `batch`
   column in `orgs.csv`; for runs longer than this session use the `headless` command `run`
   returns. Single pages: `fetch` with `org_id`; pages that need clicks: `browse`.
   A federation or association page listing many clubs' officials is gold: `fetch` it and
   assign its contacts to the right organisations.

5. **Review.** Jev grades every address from its quote. Rows marked `review=yes` (low
   confidence, address shared by several organisations, page on another organisation's
   site) do not count until you look: open the quote, then confirm or correct with
   `grade(set=[{value, org_id, grade, person, role}])`. Fill `person`/`role` on the A/B rows
   the same way. Your grades are never overwritten.

6. **Deliver.** `export(project)` writes the workbook (summary, per-batch table, contacts A
   first with page, quote and review flag, organisations with best grade and crawl status,
   venues, costs, X rows apart) and Bruce's 18-column routes CSV. Report the summary
   numbers by their exact names: A+B routes per 100 organisations, share of organisations
   with A+B, coverage (A-D), still to review, cost.

Keep going. A first pass is not the job: when `run` stops, look at what is still missing
(organisations without A/B, without a website, unreadable sites, email buttons without
addresses) and attack it - new sources, `browse`, better focus terms - then `run` again.

## Rules that stay

- Never guess or construct an email; every contact row comes from a page and keeps its quote.
- Research only: nothing is sent to anyone.
- Every organisation gets a row in the end, including "not found" and why.
- Spending: every call is logged; each paid account has a red line at 10% of its starting
  balance and is skipped below it while the others carry on. No other caps. Report cost.

Read [the playbook](../../references/PLAYBOOK.md) for Bruce's definitions, grades and the
methods that paid off in past runs.
