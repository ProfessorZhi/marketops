# US - sports fields, schools and contractors

- Product / scenario: field line-marking and turf robots for schools, parks, leagues and
  field contractors
- Unit: the organisation; merge key normalised name + state (outside the US add the
  country - a state-only key collides for every foreign organisation)
- Last updated: 2026-10-26 from Bruce's sessions

## Pool sources
| Source | What it gives | Size | Note |
|---|---|---|---|
| Non-golf mother pool | organisations with sites | 113,839 organisations; official site found for 88.72% | the site look-up was the biggest blocker (0% -> 88.72%) |
| Venue pool (US -> global) | venues | 254,000 -> 244,747 high-confidence | - |
| State school data | K-12 schools | five-state pool 15,844 schools | official state sources were behind 403 / form gates |
| League and association directories | clubs, officials | - | association boards: see global-golf for the board look-up |

## Pages that paid
- 25 accounts end to end in 19.33 minutes once each stage handed straight to the next and the
  coordinator only watched (the reference speed for a 25-account batch).
- One batch: 86 page attempts gave 21 readable pages (4.1 attempts per page). Report attempts
  and pages, not pages alone.
- Page quality by page type, not keywords: navigation, course and empty department pages were
  false positives.

## Titles and grades
- Schools: athletic director (the P1 target), facilities director; parks: parks & recreation
  director, grounds supervisor; contractors: owner / founder / president.
- Visible binding: same line, card or section with a name or department. A first pass that
  held back unnamed addresses sent 94 public addresses to manual review; the second pass gave
  53 named + 41 role routes = 102 accounts.

## Corrections
- Agriculture and contractor lines (see other-lines): measure each source on 30 accounts and
  scale, improve once or stop.

## Pitfalls
- A CSV saved with a byte-order mark made the first column unreadable and voided a batch - write
  UTF-8 without BOM.
- "50 accounts, only 4 addresses" was the user's question that exposed a dropped stage: when the
  pool is large and the output tiny, check that every stage's output was merged.
- A batch-size of 5 organisations meant 1-2 years for the pool; 1,000 per batch meant 7-14
  days. Size batches from the pool and the deadline.

## Benchmarks
| Run | Accounts | Result | Time |
|---|---|---|---|
| Bruce, non-golf B002 | 25 | 102 account-level routes, 15 qualified | 19.33 min |
