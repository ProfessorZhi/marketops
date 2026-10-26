# Bruce's six projects

What Bruce researched, at what scale, and what each project taught. Counts come from the
corpus selection (`docs/benchmarks/bruce-aggregate-062/SELECTION.md`): sessions are
de-duplicated threads, hours are first-to-last message spans (an upper bound, idle days
included). Lessons marked *to extract* are filled from his sessions with the recipe in
`docs/benchmarks/bruce-knowledge/TASK.md` - methods, job titles, sources and corrections
only; no names, addresses or phone numbers.

| # | Project | Sessions | Batches | Hours (span) | User turns |
|---|---|---:|---:|---:|---:|
| 1 | France - natural-grass pitches (mowing + line marking) | 12 | 86 | 740.9 | 422 |
| 2 | UK - pitches and school grounds, with outreach waves 1-4 and reply monitoring | 42 | 122 | 2,273.8 | 842 |
| 3 | US - sports fields | 27 | 197 | 5,859.8 | 2,534 |
| 4 | Global - golf and non-golf outdoor venues (several country pools) | 40 | - | 551.6 | 1,020 |
| 5 | Germany - sports grounds | 4 | - | - | - |
| 6 | Switzerland - sports grounds | 3 | - | - | - |

## 1. France - natural-grass pitches

- Output: a 23-column assessment table; `role_priority` prefixes are the grades
  (`A_*`, `B_related_operator`, `C_relevant_official_business_entry`,
  `D_official_general_research_entry`). 249 graded accounts: A/B/C/D = 4/67/102/76.
- Merge and dedupe were part of the work: a 16-column table keyed on
  `institution_email_key` (organisation + normalised address) with `source_batches`,
  `unique_route_count`, `duplicate_source_count`. Organisation-level grade = its best route.
- Work split into parallel sessions by product (mowing, line marking) and step (web mirror,
  literal email extraction, email role assessment, page quality).
- Batches 001-026 of 150-300 organisations (pool import), then 25 per batch (top-ups).
- *To extract*: job titles that turned out A/B, the sources that paid, corrections Bruce made.

## 2. UK - pitches and school grounds

- The only project with outreach and replies (waves 1-4, reply monitoring).
- Benchmark baseline used for 0.6.0: 344 accounts in batches 0078-0082 of the pitchfinder
  family (5,596 organisations, 6,292 accounts).
- Schools: estates or business manager; councils: parks / grounds team or the contractor.
- *To extract*: which roles replied, which sources paid, corrections.

## 3. US - sports fields

- Many sessions over a long span; an 18-column route table (the format the plugin exports).
- *To extract*: sources (state associations, parks departments, school data), titles, corrections.

## 4. Global - golf and outdoor venues

- Several country pools in parallel; 7,416 domains, no per-account route table.
- Golf is excluded from the grass-pitch projects; keep the two apart.
- *To extract*: per-country sources and what made a country pool pay.

## 5-6. Germany and Switzerland

- Swiss clubs: the Vorstand page names president, Platzwart / infrastructure and secretary
  with their own addresses; municipalities have a Sportamt / Liegenschaften page.
- Swiss benchmark: Bruce reached A+B at 61% of organisations - the plugin's breadth target.
- *To extract*: German sources (Landessportbund, Vereinsregister), titles, corrections.
