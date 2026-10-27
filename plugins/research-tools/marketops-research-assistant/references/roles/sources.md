# Role: sources

**Job**: keep the pool growing with the right organisations. The source matters most: a better
list beats a better crawler.

Read first: the brief (`project(name)`), `references/projects/<market>.md`, marketops-sources.

Loop:
1. Look at what is pooled and where it is thin: `project(name)` -> `stages`, the 数据源 sheet,
   `orgs(project, limit=...)` by segment and city. Coverage gaps (a kind of organisation, a
   district, a region) decide what you look for next.
2. Find candidate lists in marketops-sources order: a register or federation finder that returns
   the whole roster in one request, open-data exports, CMS endpoints, the client's own lists;
   search engines last (they return pages, not rosters).
3. Check 20 rows of each candidate before anything else: what is one row really (a venue, an
   operator, a company, a church)? Note the yield you expect.
4. Simple export (one file, clear columns): `fetch` it, then `pool(project, "pages/<file>", ...)`
   yourself. Anything that needs scraping, several pages or mapping work:
   `tasks(action="add", title="pool: <source> <url>", note="<what a row is, columns, filters>")`
   for the pool workers.
5. After a source has 30 organisations through the sweep, read its yield (数据源 sheet,
   `project()` source yields): under 5% A/B -> stop adding from it; note why.

Never done after one list. Every project starts its pool from several sources: one list -
even a good one, even a pool inherited from an earlier project - covers only the part of the
market that list happened to know about. A list you already have counts as one source; log it
too (`pool(...)` logs it in sources.csv, an inherited orgs.csv does not).

Done when the pool is saturated: `project(name)["stages"]["sources"]["saturated"]` - at least
three sources pooled and the last three each added under 5% new organisations (the rest merged
into ones already there) - or the brief's coverage is reached. Until then keep looking: new kinds
of list (federation finders, the national facility census, city portals, chambers of commerce,
the client's CRM), new districts, new segments. Report: sources pooled, sources queued, sources
dropped and why, the last new-share numbers.
