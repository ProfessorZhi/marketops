# France - natural-grass pitches

- Product / scenario: maintenance robots for natural grass - mowing and line marking, run as
  two separate lines
- Unit: the operating organisation (mostly communes, also clubs and contractors), merge key
  `account_id`; route key organisation + normalised address (`institution_email_key`)
- Last updated: 2026-10-26 from Bruce's sessions (12 threads, 86 batch numbers)

## Pool sources
| Source | What it gives | Size | Note |
|---|---|---|---|
| RES sports-facility register | every facility with owner and operator | mother pool 32,499 rows / 22,409 facilities | the backbone; one commune owns many facilities |
| National administration directory, `/exports/json` | every mairie with phone, address, often an email | 93,781 entries, 35,805 mairies, read in 31 s | organisation phone coverage 21.8% -> 96.7%; deliverable rows 14,663 -> 21,249 |
| BOAMP tenders | maintenance contracts and contractors | 1,029 notices | only 9 had been merged - recover before crawling |
| Contractors (SIREN) | the outsourced maintainers | 323 in the pool; 118 -> 104 after merging on SIREN | merge on the company id, not the name |
| Federation club finders | clubs per sport | - | clubs often have no listed site |

## Pages that paid
- Communes: four paths were 534 of the 911 pages Bruce opened by URL (59%): `/sports` 153,
  `/equipements-sportifs` 146, `/contact` 131, `/mentions-legales` 104.
- Of 220 mirrored pages, 44 had an address (20%); a strict "this page is about natural grass"
  filter left 5 routes from 220 pages - the filter was too strict, not the web too thin.
- Mirror pass rates of 8-20% came from page selection (which pages were picked), not reading.

## Titles and grades
- Mowing and line marking are one campaign and one list ("我是一起推的，发同一封开发信").
- Pool: Data ES one-click CSV filtered to open air + natural grass + large pitches, golf excluded.
- Three layers per organisation (decides / maintains on site / department inbox), 3-5 routes; one
  generic inbox kept for forwarding, never in an A/B slot. Rejected: ticketing, membership, fans,
  marketing, media, PR, youth sign-up, sponsorship sales.
- The bottleneck was pages with no address (4-10 usable from 50 mirrored sites), not speed.
- Graded accounts: 249 - A 4 / B 67 / C 102 / D 76 (Bruce's table, `role_priority` prefixes
  `A_*`, `B_related_operator`, `C_relevant_official_business_entry`,
  `D_official_general_research_entry`).
- Mowing line: 17 routes, none named - a role mailbox is reachable, not interested; keep it at
  its grade, do not promote it.
- Named contacts reached 9.8% of rows after the directory export.

## Corrections
- Work was split by step, not by region (mirror, extraction, page quality, role assessment,
  intake, reconciliation each in its own thread): a reading problem did not touch grading, a
  grading change reran only grading. The pipeline keeps the same separation per stage.
- Delivery columns were redefined mid-way (client's A-J + 4 dimensions + a required
  MAINTENANCE_MODEL): two rounds were not delivered. Settle the columns in the brief.

## Pitfalls
- Placeholder text written as "mirrored = success": extraction fell 49 -> 22 -> 0 while the
  dashboard stayed green. Count addresses on disk, not status fields.
- Map coordinates read as addresses (13 of 17 in one batch): an address needs an `@` and a TLD.
- 36 workers above a 24-worker limit were killed and the batch had to be redone; more workers
  made mirroring slower, a frozen queue with 3 reads per domain made it fast.

## Benchmarks
| Run | Organisations | With A/B | Named A/B | Time | Note |
|---|---|---|---|---|---|
| Bruce, graded accounts | 249 | 71 (28.5%) | - | 333 turns, 4.13 h of agent turns, 740.9 h span, 422 user messages | the reference in `references/benchmarks.md` |

## From his own instructions (supplement)

- Mowing and line marking are one research task for one site, not two projects ("只有一个调研任务").
- One pipeline end to end (2026-09-21): 网页提取 -> 网页质量 -> 网页镜像 -> 邮箱提取 / 邮箱质量 ->
  飞书入库 are stages of one run, not four threads handing over to each other. The 2026-09-15 拆分 and
  the 2026-09-16/17 "1 -> 2 -> 3 -> 4 串行交接" that replaced it were themselves replaced:
  "就一套流程走完，四套流程乱且复杂，效率一定会低". See `docs/benchmarks/bruce-knowledge/CORRECTIONS.md` POOL-01.
- Do not mirror a page that already shows no email clue. "This page has no address" is not "this
  organisation has none": check the Contact / Équipements sportifs / Annuaire entry pages first.
- Page budget per organisation: 4-6 high-signal page types, at most 6-8 pages, no site-wide
  crawl; default cross-domain concurrency 12-24, per-URL timeout 15-20 s, per-response cap 5 MB.
- The delivery sheet is the list of valid email routes, not a research log - a duplicate route
  has no place in it.
- A D-grade route stays in the table and stays marked D; it is never dressed up as a named
  contact or a field manager.

### Outreach (not this plugin)

- D-grade routes on this line are sent to, not dropped, with a lower reply expectation.
