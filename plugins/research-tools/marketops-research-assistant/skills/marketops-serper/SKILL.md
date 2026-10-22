---
name: marketops-serper
description: Serper (Google results as JSON) through MarketOps - cheap broad discovery of directories, source lists, PDFs and site-restricted role pages. Load when you are about to search for sources or staff pages.
---

# Serper: find the list and the people page

**Call**: `marketops_search(provider='serper', query, country='gb', language='en', run_id,
operation_id, mode='web', limit=10)`. Rung 1 for discovery: cheapest per query.

**Good queries** name the source type, the area and the document kind:

- Whole lists: `"{target type}" "{region}" directory`, `"{association}" members list`,
  `"{region}" "{target type}" filetype:pdf`,
  `site:{association domain} members`.
- Staff pages on a known site: `site:{domain} "{wanted role}" OR "{other wanted role}"`.
- Officers in public documents: `site:{domain} "committee" filetype:pdf`, `"{venue}" minutes secretary`.

**Modes**: `web` (default), `maps` (places with address, phone and website: the first choice for
listing an area, before SerpApi), `news`, `images`, `scholar`, `patents`, `shopping`,
`autocomplete`; `page` only where the mode paginates.

**Use the result**: titles/snippets are leads, never facts. Pass directory URLs to
`marketops_research_step` and official sites to `marketops_site_harvest` or `marketops_pool_import`.
Record how many entities a directory lists before importing it.

**Cost**: one credit per 10 results (about $0.001), so keep `limit=10`; asking for 20 bills two
credits. New accounts get 2,500 free credits once. The same query is charged once per Run even if
retried through another route.

**When it runs out**: "not enough credits" marks Serper out; `marketops_search(provider='all')`
then asks SerpApi instead and TinyFish search keeps running. Tell the user to top up Serper.

**Keep `site:` queries simple.** One phrase per query: `site:example.fr directeur`, not
`site:example.fr (partenariats OR commercial OR directeur) email`. The engine sometimes ignores
`site:` on long boolean queries and returns unrelated pages; the Paris run got Gmail and US Senate
contact pages. The merged search drops off-site hits (`off_site_results_dropped`). To read one
site thoroughly, use `marketops_site_harvest` instead of searching it.
