# Germany - sports grounds and contractors

- Product / scenario: grounds maintenance robots for municipal sports grounds and clubs;
  maintenance contractors as a second customer group
- Unit: the operating organisation (city Sportamt, club, contractor)
- Last updated: 2026-10-26 from Bruce's sessions

## Pool sources
| Source | What it gives | Size | Note |
|---|---|---|---|
| Sportstätten lists (cities, Länder) | grounds with operator | mother pool v4 34,423 | surface type is rarely given (34 rows had a true value) |
| Municipal codes (AGS) | every municipality | 10,749 | READY organisations 196; NRW coverage 4.8% |
| EU TED tenders | maintenance contractors and outsourced grounds | 2,299 contractors (89.5% with an address); 222 accounts with proof of outsourcing | the contractor line |
| Contractor association portal | members | not machine-readable (soft-404 API) | logged as a blocker, not filled by guessing |
| Statistics office | - | refused | - |

## Pages that paid
- `Impressum` and `Ansprechpartner` pages; `Vorstand` for clubs.
- The email list reached 55,200 rows x 33 columns on 9,490 domains; addresses 17,217 -> 27,253
  and turf-relevant 26.3% -> 75.3% after deep reads.
- One READY organisation needed 26.3 searches and 54.4 pages on average - addresses, not
  organisations, are the bottleneck.

## Titles and grades
- Präsident / Vorsitzender, Leiter Sportamt; Platzwart, Hauswart, Vorstand Infrastruktur.
- A title field filled from a template (69.3% of rows) left 188 real titles - titles come from
  the page.

## Pitfalls
- 288 parallel DNS look-ups were rate-limited; 183 live domains (1,033 venues) were dropped as
  dead. Limit parallel look-ups and retry before calling a domain dead.
- A free-mail filter deleted 4,586 rows where 12 were wrong: judge the local part.
- One city address was listed for 278 organisations: organisations are not inboxes - count
  both.
- A gzip response not decompressed gave 0 links on 8 big city sites with HTTP 200.

## From his own instructions (supplement)

- The 16 Länder run as parallel sub-threads under one main agent, not one long serial run.
- Do not recreate an existing skill, and do not start live research until he approves it.
- On error: record, skip, continue; report to the thread; retry after the wave.
- Anything needing his decision is batched into one numbered list in the thread and answered in
  one reply.
- Reply-monitoring interval was set to 12 h, then 6 h - a setting, and it is changed often.
- A replacement contact named in a reply is itself a new route and is sent to as well.
