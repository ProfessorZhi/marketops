---
name: marketops-discovery
description: Turn a research request into a structured brief, then build the complete source and venue/organisation pool with reconciled counts. Use at the start of a MarketOps Run or when marketops_next recommends intake or discovery.
---

# Brief, source pool, target pool

For saved JSON use artifact-mode `marketops_pool_import`: preview its schema,
then map exact fields and apply literal filters locally. Import local pages
without new provider requests; upstream pagination is separate. France Data ES
has `equip_nature` for indoor/outdoor: never filter only on INTERIEUR in the
name. Verify commune fields instead of assuming `lib_bdv` is a city. A mixed
directory does not prove every venue indoors. Supply an `indoor` source claim
and require it in the brief for indoor venue eligibility.

**Any register or open-data portal: find its bulk export before paging.** Record APIs
page slowly and often stop at a fixed depth. The Paris run read 100 pages of 100 rows on an
Opendatasoft portal for 17 minutes and still lost the last 2,772 rows past offset 10,000.
Common export endpoints:
- Opendatasoft: `/exports/json` or `/exports/csv`.
- CKAN: the resource download URL, or `datastore_search` with a high `limit`.
- Socrata: `rows.json` or `$limit`.
- ArcGIS: `query?f=geojson` with `resultOffset`.
- Many sites: a plain CSV/XLSX download link.

Rules:
- Read one regional slice per call (under 50,000 rows), keeping only the columns you need.
- Get the total first and check each slice against it.

Opendatasoft example:
- Read one department (or another slice under 50,000 rows) per call from the export
  endpoint, keeping only the columns you need. For example:
  `…/api/explore/v2.1/catalog/datasets/<id>/exports/json?where=dep_code="75" AND
  equip_nature="Intérieur"&select=equip_numero,inst_nom,equip_nom,equip_type_name,
  inst_adresse,inst_cp,com_nom,equip_gest_type,equip_prop_type`.
- Use `marketops_research_step(task_kind='known_public_url')`, then
  `marketops_pool_import` in artifact mode, one local page at a time.
- Get the total first with `records?limit=0&where=…`, then check each slice's
  count against it.
- Field names differ between datasets: preview the schema first.

**Map who runs each venue.** When the source has operator/manager and owner fields, map
them as `operator`, `operator_type` and `owner_type`.
- Contacts sit with the operator: the municipality's sports department for public
  venues, the company or association otherwise.
- `marketops_venue_groups` turns the inventory into operator targets.

## Brief

Save it with `marketops_start(..., brief=...)`. Fields:

- `target_kind`: `venue` (asset-led: pitches, halls, sites) or `organization` (account-led:
  companies, clubs, councils). `goal`: `pool` (list and qualify targets) or `contacts` (key routes
  per organisation; requires `target_kind:'organization'`).
- `require_named_person`: true only if the user asked for named people.
- `roles_wanted`: the roles the user wants, as a list (e.g. `["operations manager", "community
  manager"]`). Every harvest then has Jev tag each email with who it reaches.
- `minimum_targets`: the user's explicit number, else `null`. Do not invent a quota.
- `evidence_requirements`: kind → field names each target must have quoted, e.g.
  `{venue:['identity','hourly_rate'], organization:['identity']}`. Narrative goes in `objective`.
- Scene rules (roles, grades, exclusions) come from the user or an acceptance profile such as
  [UK natural grass](../../references/ACCEPTANCE_PROFILE_UK_GRASS.md). Never apply one market's
  profile to another market silently.
- Changing the goal later: a new brief record with `facts.supersedes`; never lower a goal to pass.

## Source pool: find the whole list first

Prefer one complete source over many guesses. In order:

1. **Bulk sources**: `marketops_search(provider='all', ...)` runs Serper and TinyFish search (two
   different indexes) on the same query and merges them (`found_by` per result) - for directories,
   association member lists, council/strategy PDFs, charity registers. A country and use case
   usually has an authority site that lists everything.
2. **Map listing**: SerpApi `marketops_serpapi_mcp_search(params={engine:'google_maps', q:'<type> in <area>'})`
   returns places with website and phone. Use it as a second, independent list and compare.
3. **Fetch** directory pages with `marketops_research_step(task_kind='known_public_url', url=...)`
   (Crawl4AI, falls back to TinyFish fetch). Follow actual links and pagination; guessing paths
   rarely works.

**Keep adding sources until the list is complete**, not until you have one list. For a region,
list its towns first (a county has 30+), then:

- member lists of the associations, leagues, chambers or federations the targets belong to, their
  Wikipedia articles (member tables), official registers (charity, company, licensing) and
  government open data;
- a site behind a challenge page stays unopened: search
  `site:<that domain> <type>` and use the result titles as names, or use another list;
- `google_maps` per town for every town in the region, not just the big ones.

An overlap of about 70% between two independent lists is a method signal, not
proof of full coverage. Complete registry pagination and reconcile geography,
venue eligibility, equipment versus facilities and operator relationships;
report unresolved coverage explicitly.

Whole-list endpoints beat crawling: association search pages, `/wp-json/wp/v2/<type>` (check the
`X-WP-Total` header), open-data portals, content-type sitemaps. See
[lessons](../../references/MEMBER_A_LESSONS.md#discovery-find-the-list-dont-guess-it).

For every source, record how many entities it lists. Before delivery the count must reconcile:
listed = saved candidates + excluded + HOLD. A gap means rows were dropped; find them.

A directory page can list many venues; one domain is not one venue; a map pin or snippet does not
prove ownership, surface, operator or an email. Zero hits is "not found with these queries", not
"no market".

## Target pool

- Register each target with `marketops_propose_batch` (≤20 per call). Input-only names may be
  saved as `facts:{name:...}, artifact_ids:[]`; they stay unverified until quoted.
- Dedupe by name + postcode/address + website domain; keep both if unsure and say why.
- Exclude out-of-scope types with an `exclusion` quote (e.g. 3G/artificial surface, golf, indoor).
- Triage at volume with `marketops_jev_triage` on the saved pages (in scope or not, type,
  priority): one call for up to 50 targets. Jev ranks and filters; it never establishes a fact.
  Read the source yourself for anything under its confidence threshold.
- Then move to `marketops-accounts`, highest-priority targets first.
