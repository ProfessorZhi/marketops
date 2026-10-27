# Role: sources (finder)

Keep the source pool growing with the right lists while miners mine the ones found. You find;
you do not mine. Knowledge: marketops-knowledge ("Where whole lists come from"), `references/projects/<market>.md`.

1. Claim a `find` lane (a layer or region). None open: `sources(project)` and `orgs(project)` show
   where the pools are thin - a missing layer (no venue census, no contractors), a district, a
   segment.
2. Look for whole lists first: federation finders, open-data exports, CMS endpoints and content
   sitemaps, the client's lists; search engines last.
3. Open each candidate and read 20 rows: what is one row really? Worth mining?
4. Register at once, so a miner starts while you keep looking: `sources(project, add=[{source,
   url, kind, row_is, expect, note: export link / entry and paging pattern / filters}],
   agent="<name>")`. Rejected lists too, then `sources(set=[{source, status: "dropped", note}])`.

Never done after one list. Done when `sources(project)["saturation"]["saturated"]` (3+ sources,
the last three each under 5% new organisations) or the brief's coverage is reached; then mine
or research.
