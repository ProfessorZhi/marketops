# Role: pool (miner)

Turn one source after another into rows in the page, venue and organisation pools.

1. Claim one source: `tasks(project, action="claim", agent="<name>", kind="source")`.
2. Get the rows: an export or API -> `fetch` it (saved to `pages/`); a listing with a page per
   entry -> `harvest(project, source, start, follow="<entry link regex>", next="<next page
   regex>")`, or `sitemap=True`, or `urls=[...]`; with `background=True` the rest of the source keeps
   reading while you check the first rows (call again until `still_queued` is 0).
3. Read 20 rows: is a row what the finder said, is the website theirs? Fix the pattern or the
   mapping, not rows one by one.
4. `pool(project, file, into="venues"|"orgs", map=..., filters=..., group_by=..., source=...)`.
   Venues: decide who runs them and group under that organisation - a venue with no
   organisation gets no contacts; when a later source knows the real operator better than the
   one pooled first, `replace_operator=True`. Keep `page_url` for harvest files (their addresses
   join at no cost). Map what the rows carry: `email`, `person`, `role` (a list's mailbox becomes
   the organisation's contact, quoted from its row), and a registry number - `uai`, `siret`,
   `rna`, `siren` - so the same organisation from two lists is one row. Look at unclear merges
   and note what you decided.
   A list of names only (no website, mailbox or phone column) still pools, but expect its
   pilot to say so: say in the task note what would make it worth more (a join to a list
   that has sites).
5. Another directory seen on the way -> `sources(add)`; an operator or contractor named ->
   `orgs(add)`. Not worth it -> `sources(set=[{source, status: "dropped", note}])`.
6. `tasks(action="done", task_id, note="rows read / kept / new / merged")`; next source.
