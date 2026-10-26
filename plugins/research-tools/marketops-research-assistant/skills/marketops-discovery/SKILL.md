---
name: marketops-discovery
description: Turn a research request into a structured brief, then build the complete source and venue/organisation pool with reconciled counts. Use at the start of a MarketOps Run or when marketops_next recommends intake or discovery.
---

# Brief, source pool, target pool

For saved JSON use artifact-mode `marketops_pool_import`: preview its schema,
then map exact fields and apply literal filters locally. Import local pages
without new provider requests; upstream pagination is separate. France Data ES
has `equip_nature` for indoor/outdoor: never filter only on INTERIEUR in the
name. Map `operator_type` to `equip_gest_type` and pass
`operator_covered_types=["Commune", "Etat", "Département", "Région"]` (the source's own
values for public management): a municipal gym without its own website is then kept as a
venue but not queued as a contact target, and `marketops_venue_groups(by='city',
operator_types=[...], name_template='Ville de {group}', import_to_pool=...)` makes its town
the target. A pool imported without it: the same call with `park_covered_in_pool=<pool_id>`
holds those rows (the rc12 Paris run waited on 707 of them). Verify commune fields instead of assuming `lib_bdv` is a city. A mixed
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

`marketops_research_step` does this by itself for Opendatasoft, CKAN and Socrata: give it the
first-page record URL with your filter, and it reads the export instead. The reply says so in
`bulk_export`; `as_is=true` reads a URL exactly as given. For other portals, find the export
yourself.

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

**A contacts goal researches every kind of organisation tied to the targets, not one.**
Unless the brief limits it, build one pool source per family and let the source gate compare
them after 30 targets each:
- operators and owners (town sports departments, management companies, chains);
- clubs and associations based at the venues or in the area (committee pages name officers
  with their own addresses: in the live Paris run they gave most of the A and B);
- schools, universities and their sports services;
- the federation or league's club list for each sport in the area.
A venue inventory alone leads only to operators, and operators mostly publish service
inboxes. Never spend the whole budget on one family before the others had their pilot.
Tag each source: `marketops_pool_import(..., family='OPERATORS' | 'CLUBS_ASSOCIATIONS' |
'SCHOOLS_UNIVERSITIES' | 'FEDERATION_LISTS')`. `marketops_next` keeps a contacts Run open
(`IMPORT_NEXT_TARGET_FAMILY`) until each family finished its pilot or you recorded why it
does not apply: `marketops_method(method=<family>, outcome='not_applicable', note=...)`.
A brief can narrow the list with `target_families:[...]` when the user limited the scope.

**Follow the vein that pays.** When a source shows `PROMOTE` in `source_yield` (for example a
committee's officer directory that prints name, role and email on one page), its *kind* is
the lead, not just that page: search right away for every sibling of the same kind in the
area (the other sports' committees, the other districts' directories, the same CMS template
on other sites: `"organigramme" comité <sport> <département>`, `site:<federation domain>
annuaire`) and import them as new sources. In the Paris run four such pages gave almost all
named A/B people while 700 venue rows gave none. A source at 0 after its 30-target pilot is
stopped, and no more targets of that kind are imported.

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
- **Scene rules: what is A, B, C or X here.** The code grades every email route from them at
  handoff, so write them before the first harvest:
  - A shipped market: `scene:'uk-natural-grass'` (packs in `references/scenes/`).
  - Any other market (Paris, a new country, a new product): write `scene_rules` yourself from the
    user's roles, in the market's language, a few title words per class:
    `scene_rules:{decision:[...], operational:[...], entry:[...], excluded:[...]}`.
    `decision` = owns the purchase (A when named, bound, email in one block); `operational` =
    runs the site day to day (B); `entry` = bookings / facilities team / venue hire (C);
    `excluded` = never a contact (finance, HR, communication, competitors). Show the user the
    lists in one line with your other intake questions; they can correct them later with a new
    brief record (`supersedes`).
  - `scene_rules` on top of `scene` replaces only the classes it lists.
  - Never apply one market's pack to another market.
- `source_gate`: leave it out. Each source's first 30 finished targets give a verdict (A+B
  accounts ≥10% PROMOTE, 5–10% change approach once, <5% STOP) as advice; you decide whether to
  leave a source. A fixed list from the user is the task: finish it whatever its yield.
  `{park: true}` makes STOP park the rest automatically; `{pilot:20}` for small pools.
- Screening (Jev, one question on each target's first page): give `screen_in_scope` (who is a
  target, one line), `screen_competitors` (who sells what the user sells) and
  `screen_out_of_scope` (kinds of body to skip). A site Jev is sure (≥0.9) is a competitor or
  out of scope is not crawled or staged; it holds with `SCREENED_OUT` and can be reopened.
  Without these the objective is used; `screen:false` switches screening off.
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
