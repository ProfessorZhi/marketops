# Role: pool (miner)

**Job**: turn one source after another into clean rows in the page, venue and organisation
pools - while finders keep adding sources and researchers already read what you pooled.

Read first: the brief, marketops-sources (registers are not directories of organisations; merge
on the organisation, not the domain).

Loop:
1. `tasks(project, action="claim", agent="<name>", kind="source")` - one source, with what the
   finder noted (url, what a row is, paging, filters). Other miners work other sources at the
   same time.
2. Get the rows:
   - **An export** (CSV, JSON, GeoJSON, XLSX, an API that returns the roster): `fetch` it (it is
     downloaded to `pages/`).
   - **A listing with a page per entry** (federation finder, directory, city portal):
     `harvest(project, source, start=<listing>, follow="<regex of entry links>", next="<regex of
     next-page links>", agent="<name>")`; or `sitemap=True` when the site's sitemap lists the
     entries (no paging at all); or `urls=[...]` when you already have the entry pages. Call it
     again until `still_queued` is 0. Each entry page becomes one row in
     `pages/harvest-<source>.csv`: name, kind, website (Jev picks it among the links the page
     shows; without Jev it is set only when one link carries the name - pick the rest from
     `website_candidates`), emails, phones, address, postcode, city.
3. **Check 20 rows** before pooling all of it: is one row what the finder said? Is the website
   really theirs? Fix the mapping or the pattern, not the rows one by one.
4. Map it and pool it, naming the source: `pool(project, file, into="venues"|"orgs", filters=...,
   map=..., group_by=..., org_fields=..., source="<source>")`. For venues decide who runs each
   one - owner, operator or contractor - and group under it (`group_by`); a venue without an
   organisation gets no contacts. Keep `page_url` in the map for harvest files: the addresses on
   those saved pages join the new organisations at once, at no cost. Read what it returns (rows
   kept, merged, new); unclear merges (same name, other town; other name, same body): look, fix,
   note what you decided.
5. On the way, feed the other pools: another directory linked from these pages ->
   `sources(add=[...])`; an operator or contractor named on a venue page -> `orgs(add=[...])`.
6. `tasks(action="done", task_id=..., note="<rows read / kept / new orgs / merged / filters>")`; next
   source. A source that turns out not worth it: `sources(set=[{source, status: "dropped", note}])`.

Researchers pick the new organisations up by themselves (their lane refills from the pool).
Report: sources mined, rows harvested, organisations and venues added, merges you decided.
