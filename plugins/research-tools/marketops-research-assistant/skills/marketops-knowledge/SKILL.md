---
name: marketops-knowledge
description: Bruce's business experience for MarketOps research and the usual blockers - who counts as a customer, the questions to settle in the brief, the A/B/C/D/X grades and each market's job titles, where each market's venues, organisations and people are, which pages pay, when to stop a source, and the failures that cost his runs most. Use when writing the brief, choosing sources, judging a contact, or when results look thin or wrong.
---

# Bruce's business experience

From Bruce's own research sessions (the `marketops-session` analysis). Single numbers come from
one session each: a guide, not a law - measure your own. Per market detail (sources, titles,
corrections, benchmarks): `references/projects/<market>.md`; the standing tests:
`references/benchmarks.md`.

## Who to find

- **The customer buys and uses the product to do the work**: venue owner, operator, grounds
  team, maintenance contractor. Whoever sells or distributes the same products is a competitor
  (keep the row, mark it, do not research it). When maintenance is outsourced, the contractor
  is the customer to reach.
- **Find the people who would want a demo, can recommend it, or can decide on it.** The person
  who runs the grounds, equipment or maintenance contract counts more than the highest title;
  title order is per product (golf ball picker: Range Manager before Superintendent).
- **Venues and organisations are different things.** A venue has an owner, an operator and
  maybe a maintenance contractor - each a possible customer; one council department runs fifty
  venues. Contacts hang on organisations; a person may be responsible for some of its venues.
- **The unit is the operating organisation**, researched once with all its venues. A name is
  not required: a clear role and a real work address is a route.

Settle in the brief, once, up front (each one cost him a rerun when left open): what is sold and
the one scenario; one country; customer vs competitor; in-house or outsourced maintenance;
scope by name (artificial turf counts with natural since 2026-09-21, indoor does not, golf is
its own line); what the client already has (lists, past replies); delivery rules (excluded
inboxes, columns); routes per organisation (default 3, at least 1).

## Grades (his contract `fjd-email-role-qualification`)

| Grade | Meaning |
|---|---|
| A | named person who decides on or owns the grounds or the purchase (head of grounds / facilities / estates, sports department head, club president, the board member for the ground), own address |
| B | leadership or operations who can act or forward (groundsman, site / venue manager, caretaker, club secretary, other board members), or that function's role mailbox |
| C | an official team inbox: bookings, sports office, department |
| D | a generic official inbox (info@, contact@) - a usable first contact, labelled D, never dressed up as a person |
| X | not a route here: sponsor, web designer, no-reply, unrelated private |

A+B is the KPI, C/D are assets, X is waste - count them apart. A role word in the address counts
(facilities@, grounds@, platzwart@, bursar@). An address listed for many organisations belongs to
one only when its card names it; a federation inbox is nobody's route. Titles come from the page
- a title guessed from structure stays empty. Free-mail domains are never excluded wholesale.
Output is called 研究有效邮箱; research never sends.

| Market | A | B | C |
|---|---|---|---|
| France | président, responsable du service des sports / des espaces verts | secrétaire, régisseur, gardien de stade, adjoint technique | service des sports, réservations |
| UK | head of grounds, facilities / estates manager, chairman, school business manager | groundsman, club secretary, bursar, site manager | bookings / lettings, parks team |
| DE / CH | Präsident, Vorsitzender, Leiter Sportamt | Platzwart, Ansprechpartner, Vorstand Infrastruktur | Sportamt, Vermietung |
| US | athletic director, parks & recreation director, owner (contractors) | field / grounds manager, league president | facility rentals, parks office |

## Where things are

**Sources, best first** (yields differ by an order of magnitude): one request that returns the
whole roster (federation finder, CMS endpoint, open-data export - Spain's golf federation 580
clubs from one sitemap, Canada 4,632 facilities in one REST call) > content sitemaps > real links
on the home page > guessed paths (6.5%). The client's own list beats every automatic path (one
snapshot: 31,262 records). A register is not a directory: check 20 rows (a charity code read
wrong nearly delivered 37,196 churches). Maps give venues, not organisations. Search engines
return pages, not rosters - last resort for a pool.

| Market | Venues | Organisations, operators | People |
|---|---|---|---|
| France | RES facility census (owner / operator per facility) | federation club finders, `recherche-entreprises` NAF 93.12Z, annuaire service-public (mairies), BOAMP / DECP (contractors) | commune `/annuaire/`, `/sports`, `/equipements-sportifs`, `mentions-legales`; "services techniques", "cadre de vie" pages; club bureau pages |
| UK | Active Places (47,648 grass pitches), Pitchfinder | GIAS (schools, trusts), charity register code 110, Contracts Finder | school staff / governance, trust estates, parish council minutes (PDF) |
| Germany | NRW open sports data, city / Land Sportstätten lists | Landessportbund club finders, TED, Vergabeportale | Impressum, Ansprechpartner, Vorstand; Bauhof / Grünflächenamt pages |
| Switzerland | municipal Sportamt lists | cantonal football association club lists | club Vorstand (Präsident, Platzwart) |
| US | TPL ParkServe, OSM | NCES / PSS schools, IPEDS, league directories, CISA .gov registry | district contact pages, then staff directories; count per district |
| Golf | - | national federation register (98.4% list a website) | the club's staff page |

**Pages, by yield**: staff / board pages 1.73 A/B per 100 pages, contact 0.18, home 0.08;
"facilities" keyword pages none. The legal page names the operator. Department pages beat
searching role titles. Clubs often live on hosting platforms (clubeo, pitchero, sportsregions):
follow them. Read 4+ pages a site - every site read that deep produced a route.

**Stop a source, not the job**: after ~30 organisations, >= 10% with A/B scale it, 5-10% improve
once, < 5% stop. Two rounds with no new route: switch source.

## The blockers that cost him most

- **One list, one kind of entity.** A pool from one list finds only what it knew - an
  organisation list gives no venues. Pool 1 is several sources, every layer the brief needs.
- **Green but empty.** A status said done while the rows were empty (mirror "success", extraction
  0). Judge by rows, pages and quotes on disk.
- **The middle layer never used up.** 18,954 rows built, 2 written back; 953 real council
  addresses dropped between steps. Every harvested file reaches the pools; every organisation
  ends with routes or a verdict.
- **Numbers that do not reconcile.** One metric moved 882 -> 1,197 -> 584 across reports ("我也
  不知道你哪个数据是真的"). One source of truth; the pool grows by exactly what its sources added.
- **Extraction before blame.** Lost `mailto:` and undecoded addresses (hit rate 1.3% -> 45% once
  fixed), gzip not decompressed (200 OK, zero emails), a DNS burst that threw away 183 live
  domains, a name key that collapsed 40 Cyrillic names into 12. Zero results on a big site is a
  parsing problem first.
- **Blocks.** Cloudflare blocks by IP - another reader or the TinyFish Agent, not retries.
- **Asking too often.** Twelve open questions held his queue for a hundred rounds: one numbered
  list, one reply. Rules repeated 3-9 times in chat belong in the brief or the project file.
- **Polling instead of working.** A heartbeat added 8 pages in 14 minutes; more workers on one
  heartbeat made it slower. Work flows by claims and finished events, never by status loops.
