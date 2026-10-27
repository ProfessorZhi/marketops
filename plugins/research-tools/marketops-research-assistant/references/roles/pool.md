# Role: pool

**Job**: turn a source into clean rows in the venue and organisation pools.

Read first: the brief, marketops-sources (registers are not directories of organisations; merge
on the organisation, not the domain).

Loop:
1. `tasks(project, action="claim", agent="<name>", kind="custom")` - take a `pool: ...` package.
2. Get the data: `fetch` the export or the list pages (several pages: fetch each, save what you
   assemble as CSV under `pages/`).
3. Map it: which column is the venue, the operator, the city, the website, the type. Filter what
   the brief excludes (golf, indoor, shops ...). Decide who runs each venue - the owner, the
   operator or a contractor - and group venues under it (`group_by`).
4. `pool(project, file, into="venues"|"orgs", filters=..., map=..., group_by=..., org_fields=...)`.
   Read what it returns (rows kept, merged, new); unclear merges (same name, other town;
   other name, same body) - look and fix the rows, note what you decided.
5. `tasks(action="done", task_id=..., note="<rows read / kept / new orgs / filters>")`.

The sweep picks new organisations up by itself. Report: sources pooled, organisations added,
merges you decided.
