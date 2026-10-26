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
| Bruce, graded accounts | 249 | 71 (28.5%) | - | 333 turns, 4.13 h of agent turns, 740.9 h span, 422 user messages | the reference for `marketops-benchmarks` |
