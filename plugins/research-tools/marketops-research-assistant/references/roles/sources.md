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

Done when: no new source would add organisations of the brief's kind, or the brief's coverage
is reached. Report: sources pooled, sources queued, sources dropped and why.
