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
