# Lessons from Member A's research runs

Distilled from the session audit (`marketops-session`: GOLDEN_PATTERNS GP-01..18, C1..C5 and 78
interventions). Read the section for the stage you are in; each lesson is what went wrong or what
worked, and what to do.

## Business definitions (set by the user; apply before anything else)

These came from repeated corrections in Member A's sessions. Each one, when missed, sent a whole
batch back for rework.

- **Optimise for reachable emails, not for venue size** (INT-010): rank and pick targets by how
  likely they publish a role email, not by how large or famous they are.
- **Customer vs competitor** (INT-052): an organisation that *buys and uses* the equipment or service
  to do the work (venue owner/operator, grounds or facilities team, contractor that maintains sites)
  is a customer target. One that *sells or distributes* the same products is a competitor: mark it
  `EXCLUDED` with reason `competitor`, never research it for contacts.
- **Know the user's business before searching** (INT-044): if the brief does not say what the user
  sells and to whom, ask once at the start; do not guess the industry from keywords.
- **One organisation, many venues** (INT-033): research the operating organisation once and attach
  all its venues; a person's name is not required when the route is role-bound.
- **The role priority list comes from the user** (INT-052, scorecard #4): use the roles the user
  named, in their order. If the task needs a role ranking and none was given, ask once; never invent
  one.
- **A switchboard is not a contact** (INT-062): a general phone or reception only counts when it is
  stated to reach the named role.
- **A sample is not the pool, a send is not a delivery** (INT-009, INT-011): report coverage of the
  whole target list, and count only what reached the delivery files.
- **Definitions change with a version** (INT-038, scorecard #5): when the user redefines what counts
  (e.g. what a valid email is), record it with `marketops_note` (`human_correction`), apply it to
  the remaining work and supersede affected records; do not silently restart the whole job.

## Discovery: find the list, don't guess it

- **Whole-list endpoints first** (GP-08): association search pages, CMS REST
  (`/wp-json/wp/v2/<type>`, often with `X-WP-Total`), open-data portals, inline JSON. One request can
  return the entire roster (e.g. 4,632 facilities from one endpoint). Then content-type sitemaps
  (`club-sitemap.xml`), then real links from the home page, and guessed paths last.
- **Follow real links** (GP-08): link-driven discovery found emails at 62–88% of sites; guessed paths
  6.5%. `marketops_site_harvest` follows links for you.
- **Each country/scene usually has an authority list** (intervention): look for it before crawling
  venue by venue.
- **Keys must match the entity** (GP-17): dedupe on normalised name + country + region, not domain
  (one domain merged 40 venues into 12); never collapse non-Latin names lossily; decide geography
  by real boundaries or verifiable signals, else `UNKNOWN`; check classification codes (charity
  code 110 is amateur sport, 108 is religious activities).

## Extraction: prove nothing is lost before paying more

- **Extraction bugs beat anti-bot spending** (GP-09): stripping tags dropped every `mailto:` address
  and the hit rate went from 1.3% to 45% once fixed. The plugin now keeps mailto addresses in the
  evidence text. Obfuscated addresses (Cloudflare `data-cfemail`, `name [at] x [dot] com`) are not
  decoded, by rule.
- **Blocked sites**: a heavy browser unblocking pass cost hours and mostly returned generic inboxes.
  Light, polite access first; if still blocked, HOLD and move on.
- **Silent drops are invisible** (GP-13): nothing errors when rows vanish. Check invariants after
  every save: union ≥ any part, parts sum to the total, declared = actual, every reference exists
  and every file is referenced. One run lost 582 emails this way; another dropped the single page
  holding the target role while reporting "done 29 pages" for 30.
- **Empty or sentinel values are failures** (GP-12), not passes: an empty extract, `None` or a
  placeholder must surface as an error.

## Accounts: what counts as a result

- The goal is valid key-role emails per organisation; one organisation can have several.
- A switchboard is not a contact; a phone counts only when it reaches a named role.
- A name is not required if the email is role-bound and real; never infer responsibility from the
  shape of an address.
- **Keep the tiers apart** (C3): A+B are the KPI, C/D are assets, X is rubbish. Count them
  separately. A rule that let generic inboxes into the main table once turned 78.8% of output into D.
- Every organisation gets a row, including `Not found` with the reason; never fill a gap with a
  placeholder name.

## Running the job

- **Measure, then scale or stop** (GP-06): test a source on 30 accounts; ≥10% account rate expand,
  5–10% optimise once, <5% stop the method (not the whole job) and move to the next source. A
  regulatory library expanded 5× yielded 2.7% and was rightly stopped. Use these thresholds when the
  user sets them.
- **Don't stop early** (interventions: "stopped again?", "is that all there is?"): a first pass is
  not completion and a sample is not the pool. Keep going until the brief's coverage or the budget
  is reached, without asking to continue.
- **Don't waste budget** ("don't burn tokens while I sleep"): skip targets with no route after a
  bounded attempt; never loop on the same source.
- **Report numbers that reconcile** ("I can't tell which numbers are real"): every count in the
  report must match the workbook, with units.
- **When rules conflict, escalate once with options** (GP-02): do not pick a criterion silently.
- **Say what you cannot do** (GP-07) before switching approach; never imply coverage you did not
  achieve. A negative result states its scope, hit count and cause (GP-03).
- **Never rewrite history** (GP-05): corrections are new records that supersede the old ones.
- **Out-of-scope work** (GP-18): in a read-only or narrow task, do not "fix while you are there".
