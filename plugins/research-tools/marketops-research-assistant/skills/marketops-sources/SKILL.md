---
name: marketops-sources
description: Where the people are in each market and which sources to pool first - the authority lists, open registers, federation and association directories, and the methods that paid off in Bruce's research projects. Use when building the venue and organisation pools or when yield is low.
---

# Where the people are

## Yield by source family (Bruce's past runs)

| Source family | Share of organisations with A/B | What to read |
|---|---|---|
| The organisation's own site | 45% | contact, committee / Vorstand / bureau, Impressum / mentions légales, venue-hire pages, PDFs |
| Association / federation directory | 19% | district / county FA, league, cantonal association club pages that list officials |
| School site | 12% | staff and governance pages; many only show an office inbox |
| Council / municipality | 9% | usually a contact centre; go for the sports, parks or property department, or the grounds contractor |
| Procurement notices | 0% | a timing signal only: the named contact is a buyer |

A federation or directory page that names the club next to the address counts like the
club's own page (it is graded and counted, its evidence score is lower).

## Authority lists to pool first

Each country has a register or club finder that returns the whole population. Find it
before visiting venues one by one, `fetch` the export, `pool` it, then add the others.

| Market | Start with |
|---|---|
| France | Recensement des équipements sportifs (RES, data.gouv.fr / data.es.sports.gouv.fr: every venue with its owner and operator); federation club finders (football, rugby...); the Journal officiel / RNA association register; city portals listing municipal venues |
| UK | Get Information About Schools (GIAS); county FA and league club directories; council parks and leisure pages; Sport England Active Places |
| Germany | Landessportbund and Kreissportbund club finders; Vereinsregister; city Sportamt venue lists |
| Switzerland | cantonal football association club lists; municipal Sportamt / Liegenschaften pages |
| US | state high school athletic associations; city and county parks & recreation departments; NCES school data; league directories |

A big city has far more organisations than one register: the Paris RES export alone gave
738 organisations; clubs, schools and private chains came from other lists.

## Methods that paid off

- **Whole lists first.** One association search page, CMS endpoint (`/wp-json/wp/v2/...`),
  open-data export or sitemap can return the entire roster. Then real links from the home
  page; guessed paths last (links found emails on 62-88% of sites, guessed paths on 6.5%).
- **Measure, then scale or stop.** Try a source on ~30 organisations: 10% or more with A/B,
  expand it; 5-10%, improve it once; under 5%, move on to the next source.
- **Follow the vein.** When one page type pays (a committee directory with names, roles and
  emails), search for every sibling of that kind in the region.
- **Dedupe on the entity** (name + place), not on the domain: one domain once merged 40
  venues into 12. A portal shared by many organisations is nobody's own site.
- **Clubs often have no listed website**, or one on initials: the pipeline looks it up from
  the name and town; a club found nowhere is often on its federation's page.
- **Silent drops are the worst bug.** Counts must reconcile: every organisation ends with
  contacts or a "not found" reason.
- **Don't stop early.** A first pass is not completion and a sample is not the pool; skip a
  target after a bounded attempt rather than looping on it.

Project-by-project lessons: [Bruce's six projects](../../references/PROJECTS.md).
