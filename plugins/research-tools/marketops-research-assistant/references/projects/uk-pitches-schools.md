# UK - pitches and school grounds

- Product / scenario: pitch line-marking and grounds robots for clubs, councils and schools;
  the only line Bruce took through outreach and reply monitoring (waves 1-4)
- Unit: the organisation (club, school or trust, council); merge key normalised full name +
  country + county - never the domain
- Last updated: 2026-10-26 from Bruce's sessions

## Pool sources
| Source | What it gives | Size | Note |
|---|---|---|---|
| Client's register snapshot | pitches and their operators | 31,262 records handed over in one file | ask for it first |
| Active Places | operating natural-grass pitches | 47,648 | the public alternative |
| GIAS | every school with phone and head | - | schools: staff and governance pages |
| Charity register (CCEW) | clubs registered as charities | Amateur Sport is code **110** (108 is religious activities: 37,196 churches nearly delivered) | narrow further: 39,862 -> 20,049 -> 3,463 |
| Commercial registers | companies | official-site rate 5.7% | not a directory of organisations |
| Tender platforms | councils' and academy trusts' grounds contracts | tenders 703 -> 934 | trusts publish here, not on their sites |
| County FA / league finders | clubs with officials | - | - |

## Pages that paid
- People pages first: staff / team pages 1.73 A/B per 100 pages, contact pages 0.18, home 0.08;
  529 "grounds / facilities" keyword pages gave none.
- Links from the home page found addresses on 62-88% of sites; guessed paths 6.5%.
- After fixing extraction (`mailto:` stripped with the HTML, Cloudflare-encoded and `[at]`
  addresses not decoded) the hit rate went 1.3% -> 45%: 1,739 more domains with an address.
- Light anti-blocking (warm session, real browser headers, slow rate) reached 72% of blocked
  sites with addresses on 70%; a browser (CDP) pass managed ~4 domains a minute with 96%
  connection resets and was dropped.

## Titles and grades
- Schools: estates or business manager, bursar; councils: parks / grounds team or the
  contractor; clubs: chairman, secretary, groundsman.
- In-house vs outsourced maintenance measured on 1.7% of domains; "in-house" had been
  inferred, not read, and fell from 522 to 8 when checked - read it from a page or a tender.

## Corrections
- Objective changed from "venue size first" to "address visibility first".
- School ROI watch: 0 of 8 first candidates usable; a two-signal pre-filter then gave 10
  routes; hit rate 6.45% against an expected 20% - set expectations per scenario.

## Pitfalls
- A variable reused in a merge script overwrote a 2.8 MB evidence file while the log printed
  "16,303 domains covered" - check file sizes and timestamps after a merge.
- 60 threads overwrote one global base-domain value: link discovery went 4 -> 192 once fixed.
- 7,426 "failures" were not failures: read the content, not the exit code.
- Coordinates put 223 English rows in Scotland: use the postcode area, not coordinates.

## Benchmarks
| Run | Organisations | With A/B | Note |
|---|---|---|---|
| Bruce, pitchfinder batches 0078-0082 | 344 accounts | - | plugin 0.6.0 passed all criteria (11.3 A+B routes per 100) |
| Bruce, GB deep research | 7,638 rows (32 columns) | sendable 5,137 / 4,978 unique addresses | - |

## From his own instructions (supplement)

- Build the England pitch pool from the official register first; do not start with email lookup
  and do not batch-crawl clubs.
- Research the operating body rather than the pitch: grounds / estates / facilities / venue
  operations leads.
- Golf, artificial turf and indoor sites are out of this line.
- A/B/C/D all mean "a researched, follow-up-valid route"; D stays in the table and is marked,
  never relabelled as a named contact.
- One pipeline with four stages, not four parallel process variants; classify into the delivery
  table at the end. Complexity alone was named as the cause of low throughput.
- When counting effective replies, apply his 16-type exclusion list; a reply that names a
  replacement contact is a routing event, not just a reply.
