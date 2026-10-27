# Switzerland - natural-grass football pitches

- Product / scenario: natural-grass maintenance (mowing, line marking) for football clubs and
  municipalities
- Unit: one organisation with all its pitches ("one owner, many venues"); a person's name was
  not required for a route to count
- Last updated: 2026-10-26 from Bruce's sessions

## Pool sources
| Source | What it gives | Size | Note |
|---|---|---|---|
| Mother pool | pitches and operators | 16,641 -> 2,893 candidates -> 2,865 valid -> 115 batches | "valid address" was redefined three times, each time a rerun |
| Cantonal football association club lists | clubs | - | the club's Vorstand page names its officers |
| Municipal Sportamt / Liegenschaften pages | municipal grounds | - | - |

## Pages that paid
- Club Vorstand pages: president, Platzwart / infrastructure, secretary with their own
  addresses.
- Some batches failed outright (one batch: 20 pages all failed; another: all 403) - the yield
  of a batch fell from 72.73% to 26.11% as easier sites ran out.

## Titles and grades
- His own trilingual table (the only title list he wrote, thread 01a066c5):
  | | A - can push line marking, turf, purchase, a demo | B - runs the ground | C inboxes |
  |---|---|---|---|
  | DE | Geschäftsführer, Direktor, Leiter Sportanlagen | Platzwart, Greenkeeper, Mitarbeiter Sportanlagen, technischer Mitarbeiter | sport@, sports@, sportanlagen@, facility@, facilities@, grounds@ |
  | FR | Directeur, Responsable des installations sportives, intendant, responsable des sports | Responsable entretien, agent d'entretien des terrains | same |
  | IT | Direttore | Addetto manutenzione, custode impianti | same |
  German-speaking areas have a separate ground keeper (Platzwart); French and Italian fold it into
  intendant / custode - there, look towards the maintenance contractor.
- Generic info@ / contact@ / admin@: "recorded, not counted as valid" for football (he reversed it
  once the next day); the golf line sent 92 generic inboxes the same week. Settle it in the brief.
- Surface must come from an official source (Naturrasen / gazon naturel / erba naturale), never
  inferred from satellite images or the sport. The football line is the one he stopped for ROI.
- Football batch result: 66 routes - A 16 / B 26 / C 24 (the deliverable unit is a role
  bound to an organisation).

## Corrections
- Scan per owner, not per pitch; the name stopped being a requirement.

## Benchmarks
| Run | Organisations | With A/B | Note |
|---|---|---|---|
| Bruce | - | 61% of organisations | breadth target for the plugin |
| Plugin 0.6.1 | - | 34% | 134.1 A+B routes per 100 organisations |

## From his own instructions (supplement)

- Research and sending are fully separated: no send history, no sendability decision, no queue,
  no sending. The output is "researched-valid email", never "sendable" or "net new sendable".
- A person's name is not required: role plus a work mailbox is a route.
- 20 pages per batch by default, scaled only after the output is checked.
- Authorise once. After the pool is read, pre-check, mirroring and the batch start without a
  second confirmation request.
- "Valid address" was redefined three times and each redefinition forced a full rerun: settle it
  in the brief.
