# Golf clubs, many countries

- Product / scenario: course maintenance equipment for golf clubs and their maintenance
  contractors; one run per country
- Unit: the club (one club, several courses); merge key normalised full name + country +
  region - a shared domain is a group signal, not a key
- Last updated: 2026-10-26 from Bruce's sessions

## Pool sources
| Country | Source | Size | Note |
|---|---|---|---|
| Spain | federation `club-sitemap.xml` and open `/wp-json/wp/v2/club` | 580 | whole roster in one request |
| Canada | federation REST `golf-facility` type | 4,632 | one request - the largest single source |
| Norway | federation search page | 174 | one GET |
| Finland | club list inlined (base64) in one page | 133 with address and phone | - |
| Netherlands | course API | - | 3 parameter sets failed: stopped, not crawled site by site |
| US | national course database behind a WAF | - | three rounds lost until the real endpoint was found |
| 8 countries | probing by page shape | 94 probes in 1 min 16 s; sitemap shape in 6 of 8 | probe shapes before writing a scraper |

Mother pool: 16 countries, 23,788 rows.

## Pages that paid
- Regional association boards: after fixing four extraction gaps (a CMS pattern not matched,
  board pages hosted on another platform not followed, `/page-<n>` links dropped, candidate
  pages not de-duplicated) one board look-up went from 25 to 257 contacts.
- An association account had no email field at all: the bottleneck was the source, not the
  login.

## Pitfalls
- A key regex that dropped non-Latin letters merged 40 Russian clubs into 12 rows while every
  check stayed green - never normalise a key by deleting letters.
- Addresses of 28 Croatian rows were read from page footers (the site's, not the club's).
- Bounding boxes put 423 rows in the wrong country (96% error in Norway): use the address,
  postcode or the site's country.
- Domain as merge key swallowed distinct clubs into a group's domain: 59 collisions became 5
  real merges after keying on name + country + region.

## From his own instructions (supplement)

- One run per country, frozen before the next country starts.
- **No title list from Bruce for golf**: in his own words the line has none (customer: "golf
  courses, to book a demo"; he sent country lists and screened replies). A ball-picker order seen
  in the sessions (Range Manager -> Director of Golf -> Golf Operations, Superintendent not
  first; Equipment Manager higher for the ATX line) came from agent proposals, not from him -
  confirm titles with the user before ranking.
- Non-golf venues are excluded from this line by name (they belong to `other-lines.md` /
  `uk-pitches-schools.md`). Artificial turf is not a reason to exclude a venue: from 2026-09-21
  outdoor artificial-turf pitches are in scope on the non-golf lines.

### Outreach (not this plugin)

- A reply that names a replacement contact is a routing event: that address is added and sent to.
- Failures are recorded and skipped, the wave finishes, then the skipped ones are retried in a
  second and third pass.
- Reply monitoring lives in the sending thread; the 15-minute send-progress report stops when the
  queue empties (the 15-minute line was switched off on other lines on 2026-08-23).
