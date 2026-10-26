# Playbook: what Bruce's research taught

Distilled from Bruce's research sessions (the `marketops-session` audit: golden patterns,
corrections and benchmark baselines). Advice, not gates: the user's brief always wins.

## What counts

- **Optimise for reachable people, not venue size.** Rank targets by how likely they publish
  a role email, not by how famous they are.
- **Customer vs competitor.** Whoever buys and uses the product to do the work (venue owner,
  operator, grounds/facilities team, maintenance contractor) is a customer. Whoever sells or
  distributes the same products is a competitor: keep the row, mark it, do not research it.
- **One organisation, many venues.** Research the operating organisation once.
- **A switchboard is not a contact** unless the page says it reaches the role.
- **Grades** (Bruce's contract, `fjd-email-role-qualification`; the plugin's default):
  - A: a named person who decides on/owns the grounds or the purchase.
  - B: someone in leadership or operations who can act on or forward it (groundsman,
    Platzwart, site/venue manager, infrastructure lead, board/committee member, club
    secretary), or a role mailbox for that function.
  - C: an official team entry (bookings, sports office, department inbox).
  - D: a generic official inbox (info@, office@).
  - X: not a route for this organisation (sponsor, web designer, no-reply, unrelated private).
  A+B per 100 organisations is the headline; C/D are delivered too and count for coverage.
  Report them separately - a run once turned 78.8% of its output into D and looked big.

## Where the people are (measured, past runs)

| Source family | A/B account rate | Notes |
|---|---|---|
| Organisation's own site | 45% | contact, Vorstand/committee, Impressum, venue-hire pages |
| Association / federation directory | 19% | county FA, league, cantonal association club lists with officials |
| School site | 12% | staff and governance pages; many only an office inbox |
| Council / municipality | 9% | often a contact centre; try the sports or property department, or the grounds contractor |
| Procurement notices | 0% | timing signal only; the named contact is a buyer |

Swiss clubs: the Vorstand page names president, Platzwart/infrastructure, secretary with
their own addresses; municipalities have a Sportamt/Liegenschaften page. UK schools: estates
or business manager; councils: parks/grounds team or the contractor.

## Methods that paid off

- **Whole lists first.** One association search page, CMS REST endpoint (`/wp-json/wp/v2/...`),
  open-data export or sitemap can return the entire roster. Then real links from the home page;
  guessed paths last (link-driven found emails on 62-88% of sites, guessed paths 6.5%).
- **Each country has an authority list** (federation club finder, sports facility register):
  look for it before visiting venues one by one.
- **Measure, then scale or stop.** Try a source on ~30 organisations: >=10% with A/B expand it,
  5-10% improve once, <5% move to the next source (not stop the job).
- **Follow the vein.** When one page type pays (a committee directory with names, roles and
  emails), search for every sibling of that kind in the region.
- **Dedupe on the entity** (name + place), not on the domain: one domain once merged 40 venues
  into 12.
- **Silent drops are the worst bug.** Counts must reconcile: every input organisation ends with
  contacts or a "not found" reason.
- **Don't stop early.** A first pass is not completion; a sample is not the pool. Keep going until
  the list is done; skip a target after a bounded attempt rather than looping on it.
- **Corrections are new facts.** When the user redefines a grade or scope, update the brief,
  regrade (`grade(regrade=True)`), keep going.
