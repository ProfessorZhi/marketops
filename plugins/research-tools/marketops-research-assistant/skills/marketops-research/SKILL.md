---
name: marketops-research
description: Market research for sales with the marketops tools - from a public dataset to a venue pool, an organisation pool and graded contacts with evidence, delivered as Excel. Use for any lead, venue, account or contact research request (e.g. "Swiss natural-grass football pitches, find the people who look after the grounds").
---

# MarketOps research

You do the research; the tools give you reach. Eight tools, one project folder
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
| `export(project)` | the Excel workbook |

## The flow

1. **Brief.** Ask at most once, up front, for anything missing: what the user sells, the
   region, the kind of venue/organisation, the roles that matter. Save it with
   `project(name, brief={goal, region, exclude, grades})`. `grades` describes A/B/C/D/X in
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

4. **Contacts.** `crawl(project, "orgs.csv")` reads every site in the background (home plus
   the contact, imprint, team, board and venue pages it links to; all three readers in
   parallel) and writes every email and phone with the lines around it to `contacts.csv`.
   Check progress with `project(name)`; call `crawl` again to resume. For organisations with
   no or weak results: `search` for their staff/board/committee page or PDF and `fetch` it
   with `org_id`; for pages that need clicks, `browse`. A federation or association page that
   lists many clubs' officials is gold: `fetch` it and split the contacts to the right
   organisations with `grade(set=...)` notes or by editing the CSV.

5. **Grade.** `grade(project)` lets Jev grade every new email from its quote. Then read the
   A/B rows yourself and fill `person`/`role` with `grade(set=[{value, person, role}])`;
   correct any grade you disagree with the same way (your grades are never overwritten).

6. **Deliver.** `export(project)` writes the workbook: summary, contacts (A first, every row
   with its page and quote), organisations (best grade, crawl status, "not found"), venues,
   costs, and the X rows on their own sheet. Tell the user the numbers from the summary
   sheet: organisations, A+B organisations per 100, coverage, cost.

## Rules that stay

- Never guess or construct an email; every contact row comes from a page and keeps its quote.
- Research only: nothing is sent to anyone.
- Every organisation gets a row in the end, including "not found" and why.
- Spending: every call is logged; each paid account has a red line at 10% of its starting
  balance and is skipped below it while the others carry on. No other caps. Report cost.

Read [the playbook](../../references/PLAYBOOK.md) for Bruce's definitions, grades and the
methods that paid off in past runs.
