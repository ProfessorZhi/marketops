---
name: marketops-serpapi
description: SerpApi official MCP (embedded in MarketOps) - Google Maps listings of venues with website and phone, plus other search engines. Load when you need an area's places or a structured engine result.
---

# SerpApi: list every place in an area

**Call**: `marketops_serpapi_mcp_search(run_id, operation_id, params={...})`. The official SerpApi
MCP client is built into the plugin; nothing else to install.

**Venue listing** (the main use):
`params={engine:'google_maps', q:'village hall near Chelmsford, Essex', hl:'en', gl:'uk'}`
returns ~20 places with name, address, website, phone and `place_id`. To cover a county, query
each town or grid square separately and import each result page with `marketops_pool_import`
(`listed_count` = places returned). Compare against a directory list: the two together catch gaps.

**Other engines**: `google` (organic results), `google_news`, `bing`. Avoid `google_light` when you
need real result URLs.

**Rules**: `place_id`/`data_id` are vendor ids, not MarketOps keys. A pin proves location, not
ownership, surface or an email: confirm on the official site. Map websites are often `http://`;
harvest tools upgrade them to HTTPS.

**Cost**: one search per call (paid credits); more expensive than Serper, so use it for listings,
not for every query.
