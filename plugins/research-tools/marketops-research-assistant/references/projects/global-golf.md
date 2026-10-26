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
