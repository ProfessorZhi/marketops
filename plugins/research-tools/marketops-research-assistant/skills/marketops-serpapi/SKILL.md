---
name: marketops-serpapi
description: SerpApi official MCP (embedded in MarketOps) - Google Maps listings of venues with website and phone, plus other search engines. Load when you need an area's places or a structured engine result.
---

# SerpApi: list every place in an area

**Call**: `marketops_serpapi_mcp_search(run_id, operation_id, params={...})`. The official SerpApi
MCP client is built into the plugin; nothing else to install.

**Venue listing** (the main use):
`params={engine:'google_maps', q:'<target type> near <town>, <region>', hl:'<lang>', gl:'<country>'}`
(e.g. `'coworking space near Austin, TX'`, `'village hall near Chelmsford, Essex'`)
returns ~20 places with name, address, website, phone and `place_id`. To cover a region, query
each town or grid square separately and import each result page with `marketops_pool_import`
(`listed_count` = places returned). Compare against a directory list: the two together catch gaps.

**Other engines**: `google` (organic results), `google_news`, `bing`. Avoid `google_light` when you
need real result URLs.

**Rules**: `place_id`/`data_id` are vendor ids, not MarketOps keys. A pin proves location, not
ownership, surface or an email: confirm on the official site. Map websites are often `http://`;
harvest tools upgrade them to HTTPS.

**Cost and scarcity**: on the free plan it is 250 searches a month (resets on the 1st); paid plans
are $15–25 per thousand, about 15× Serper. Before a listing sweep, check what is left with
`marketops_costs(action='balances')` and plan the sweep to fit (one query per town, not per street).
Use it for: areas where Serper `maps` returned too few places, other engines (Bing, Baidu, Yandex,
Naver, Google Jobs), and as Serper's stand-in when Serper is out. Do not use it for plain Google
web queries Serper can answer.

**When it runs out**: it is marked out until the 1st of next month; Serper `maps` and TinyFish
search carry on. Say so in the report.
