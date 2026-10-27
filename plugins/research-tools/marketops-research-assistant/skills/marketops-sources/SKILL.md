---
name: marketops-sources
description: Where to find the organisations and the people in a MarketOps research job - which registers, federation finders, open-data exports and page types pay, in what order to try them, and when to stop a source - distilled from Bruce's research sessions. Use whenever you build or extend the venue and organisation pools, pick which pages to read, or yield is low.
---

# Where the organisations and the people are

Every number here comes from Bruce's sessions as analysed in `marketops-session`
(`docs/GOLDEN_PATTERNS.md`, `derived/l2/*`, `docs/FINAL-REPORT.md`); the project files in
`references/projects/` give them per market. Numbers from one session are a guide, not a law:
measure your own (the workbook's "数据源" and "过程（耗时）" sheets).

## 1. Building the pool: whole lists first

Try discovery channels in this order - the yields differ by an order of magnitude:

1. **One request that returns the whole roster**: an association or federation search page,
   a CMS endpoint (`/wp-json/wp/v2/<type>`), JSON inlined in the page, an open-data export.
   Seen: Spain's golf federation 580 clubs from one sitemap + open WP REST; Canada 4,632
   facilities in one REST request; Norway 174 clubs in one GET; Finland 133 with address and
   phone inlined in one page.
2. **Content-type sitemaps** (`club-sitemap.xml`, not the whole-site sitemap). A sitemap pass
   gave +189% pages on single-page sites in one US run.
3. **Real links from the home page** (anchor text + URL): emails found on 62-88% of sites.
4. **Guessed paths**: 6.5%. Last resort.

In the plugin: (1) an export or endpoint -> `fetch` (downloads it) then `pool`; (2) a content
sitemap -> `harvest(sitemap=True, follow=...)`; a listing with a page per entry ->
`harvest(start, follow, next)`; (3) a page of links -> `harvest(start)` without `follow`.
Finders register each list with `sources(add=...)` the moment it looks worth it, so a miner
starts on it while they keep looking - finding and mining never wait for each other. Bruce's
pools that worked were built this way: "upstream materialises, the middle consumes, downstream
verifies"; the ones that stalled had everyone fetching everything.

Then:
- **Ask the client for the lists they already have** before crawling - one hand-given
  register snapshot brought 31,262 records in the UK runs.
- **Reuse before you crawl.** A reuse audit found role mailboxes already captured for 474 of
  985 Dutch domains and halved the crawl. The plugin's library does this for pages and
  searches; check `project()` library stats before planning a big read.
- **An official register is not a directory of organisations.** Commercial registers gave an
  official-website rate of 5.7% (GB) and 4.2% (AU); a classification code read wrong (charity
  code 108 "religious activities" instead of 110 "amateur sport") nearly delivered 37,196
  churches. Check what a register's rows really are on 20 rows before pooling all of it.
- **Merge on the organisation, not the domain**: name + place is the key; a website is a hint.
  A domain key merged 40 venues into 12 in one run and 59 "duplicates" were 5 real ones in
  another; a city portal or a club platform would merge hundreds. The plugin keeps two
  organisations apart when their websites are different pages or hosts.
- **Recover what is already on disk.** Tender notices (BOAMP: 1,029 rows, 9 merged) and a
  whole "other" bucket of a crawl were dropped without a crawl ever being needed.
- **Stop a source that does not pay, not the job** (Bruce's rule, now built into `run`):
  sample ~30 organisations; >=10% with A/B scale it; 5-10% improve once; <5% stop spending on
  it and move to the next source. Seen: association directory 17.65% and an operators'
  association 26.67% scaled; a state contractor list 3.33% stopped; a 39,936-document
  regulation library gave 16 matches (2.7%) and was stopped. A pool of 128,685 organisations
  with no yield check produced nothing sendable.
- **More stop lines from his sessions**: two rounds in a row with zero new routes from one
  source - switch source; 15 consecutive misses - the source has failed for this job; take 1-2
  sources per source family per batch rather than all of one family.
- **The source matters most** ("源头最重要"): a better list beats a better crawler. When yield is
  low, look for a better source before tuning pages or prompts.
- **Search engines are the last resort** for building a pool - they return pages, not rosters,
  and local results (Bing in particular) were poor. Use search to find one organisation's site
  or a roster page, not to enumerate a market.
- **Maps give venues, not organisations**, and bounding boxes put rows in the wrong country
  (96% error in Norway). Place by address, postcode or the site's country.

## 2. Reading an organisation: which pages

- **Page type is the yield model.** Staff / team / board pages carried 1.73 A/B per 100 pages,
  contact pages 0.18, home pages 0.08; 529 "grounds / facilities" keyword pages gave none.
  The plugin reads people pages first, then contact and legal pages.
- **The legal page names the operator**: France `mentions-legales`, Germany / Switzerland
  `Impressum`, UK "about / governance".
- **French communes: four paths cover 59% of what Bruce opened** - `/sports`,
  `/equipements-sportifs`, `/contact`, `/mentions-legales`.
- **Clubs on hosting platforms**: club and association sites often live on a platform domain
  (clubeo, pitchero, wildapricot, sportsregions, assoconnect...). Following them, plus three
  other extraction fixes, took one board look-up from 25 to 257 contacts. The plugin follows
  contact / committee links onto these platforms.
- **Directory pages list many organisations**: one directory page gave 434 addresses, 430 of
  them belonged to other organisations. Count an address for the organisation only when its
  name stands next to it (the plugin does).
- **Department pages, not role names**: on French department and commune sites the "cadre de
  vie" (11.0%) and "services techniques" (9.4%) pages carried the routes; searching for a role
  title found none. In Germany attribute a `bauhof@` / `gruenflaechen@` address by the
  department page it stands on, not by searching the mailbox name. On French sites `/annuaire/`
  (the services directory) was the page type with the most addresses (1,401 hits); service
  directory / organigram pages out-produced technical-service pages 38 to 1.
- **Contact pages vs staff directories in the US**: contact pages hit on 93% of reads while
  taking 8% of them; staff directories took 52% of reads and hit on 46% - and 90% of a school
  staff directory is teachers (about 10 teachers per decision maker: structural, not a crawl
  failure). Small pages were 5.8x denser in top routes (23.4% vs 4.0%).
- **Read deep enough**: one run averaged 2.0 pages per site and 63.4% of sites got only the
  home page; every site read to 4+ pages produced a top route.
- **Check the entry pages before giving up on an organisation**: home, contact, legal and the
  people page. Small French clubs often exist only on Facebook or the federation page (11%) -
  the federation page is the route.
- **Bind on the same page**: name or department in the same line, card or section beats
  stitching a name from one page to an address on another. But do not over-gate: a run that
  held back every address without a visible name sent 94 real public addresses to manual
  review; binding to the visible name or department gave 53 named + 41 role routes.

## 3. Before blaming the site, check the extraction

Extraction bugs cost an order of magnitude, blocking costs linearly:
- `mailto:` links lost when HTML is stripped, Cloudflare `data-cfemail` and `name [at] x
  [dot] com` not decoded: hit rate 1.3% -> 45% after the fix (the plugin decodes all three).
- A gzip response not decompressed: HTTP 200, normal size, zero links and zero emails on 8 big
  city sites.
- A DNS burst (288 lookups at once) was rate-limited and 183 live domains (1,033 venues) were
  thrown away as dead. A failure is not a dead site: the plugin retries rate limits, server
  errors and dropped connections once.
- A browser (CDP) read 2-2.5 domains per minute against 100+ for plain fetches, and 96% of its
  failures were connection resets. Plain readers first; a browser only for pages that need it.
- Cloudflare blocks by IP (the response carries a Ray ID): another header will not help, another
  reader or the TinyFish Agent might.

## 4. Market starting points

Per market detail, sources and titles: `references/projects/` (France, UK, US, Germany,
Switzerland, global golf, other lines). Sources Bruce measured, by market:

| Market | Pool from | People from |
|---|---|---|
| France | RES sports-facility register (owner / operator per venue); `api-lannuaire.service-public.fr` (35,943 mairies - match the commune by name); `recherche-entreprises.api.gouv.fr` filtered on NAF 93.12Z (sports clubs); Data ES; federation club finders; BOAMP `donnees` (64% of notices carry an address) and DECP for contractors | commune `/annuaire/`, `/sports`, `/equipements-sportifs`, `mentions-legales`; department "cadre de vie" / "services techniques"; club bureau pages; federation club pages |
| UK | the client's register snapshot; Active Places (47,648 operating grass pitches); Pitchfinder OpenActive feed (67.7% with a site email); GIAS / DfE (52,578 schools with websites and their trusts - the multi-academy-trust layer covers 30% of domains); charity register code 110; Contracts Finder by CPV over the full time window | school staff and governance pages; trust estates pages; parish council minutes (PDF) name the groundsman and contractor; council parks |
| Germany | NRW open sports-facility data (`pageSize=20000`: 17,206 organisations, 79.2% with an address); Sportstätten lists of cities and Länder; Landessportbund club finders; EU TED search (no login; 89.5% with an address); small municipal contracts only on the Vergabeportale and service.bund.de | `Impressum`, `Ansprechpartner`, `Vorstand`; municipal department pages (Bauhof, Grünflächenamt) |
| Netherlands | DUO open data (CKAN, column `INTERNETADRES` gives the school's site); TenderNed (387 of 388 notice PDFs carry an address) | school and municipality contact pages |
| Switzerland | cantonal football association club lists; municipal Sportamt pages | club Vorstand (president, Platzwart, secretary) |
| US | NCES CCD (public) and PSS (private) schools, IPEDS (colleges), College Scorecard; TPL ParkServe and OSM for parks; the CISA `.gov` domain registry to find official sites; league directories (state sources often sit behind 403 or forms) | district contact pages first, then staff directories; athletic director, facilities, parks pages - count routes per district, not per school |
| Australia | ABN bulk extract (pool 6,772 -> 65,776 rows, contractors included); it has no industry code - filter by entity type and name | school and council contact pages |
| Italy / Poland | Italy: RES facility register - 334,436 items, only 14.0% are turf; count turf blocks, not items. Poland: RSPO official CSV (56,211 schools) as the mother pool, not school-by-school pages | school and commune contact pages |
| Golf, any country | the national federation or association register (98.4% list a website); regional chapter roster PDFs; national superintendent directories are login- or paid-only - do not plan on them | the club's own staff page; regional association boards |
