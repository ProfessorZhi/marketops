# Acceptance profile: UK outdoor natural-grass sports venues

Compiled from `profiles/uk-acme-natural-grass/` (role-matrix, grading, profile) and Member A's
corrections. Apply it only when the brief is this scene; other markets need their own profile or
the user's rules. Grades are candidate assessments for human review, not approvals.

## Targets

Organisations that own, operate or maintain outdoor natural-grass pitches: schools and
universities, councils/parks, sports clubs, trusts, stadiums. One organisation may run several
venues; owner, operator, maintainer and buyer can differ.

Exclude (with an `exclusion` quote): golf and footgolf, artificial grass / 3G / 4G / 5G, hybrid
without independent natural-grass evidence, indoor venues, MUGAs, outside the declared region.

## Where the whole lists are (try in this order)

- County community-building directories (Rural Community Councils, e.g. Essex RCC "find a village
  hall" by district) - found by Serper in the live Essex run.
- County FA club lists and league fixtures pages; county cricket boards.
- Council playing pitch strategies and assessments (PDF tables of every pitch site).
- Sport England Active Places; independent schools and university sports pages.
- Charity Commission register: amateur sport charities use classification code 110 (108 is
  religious activities - a past mix-up).
- SerpApi `google_maps` per town ("sports ground", "playing fields", "cricket club", "village hall
  near {town}") to fill gaps and cross-check.

## Query templates (Serper)

- `"{county}" "playing pitch strategy" filetype:pdf`
- `site:{domain} "Grounds Manager" OR "Head Groundsman" OR "Head of Grounds" OR "Estates"`
- `site:{domain} "committee" OR "trustees" "secretary"`
- `"{club name}" "groundsman" {county}`

## Walls seen in this scene

- Council sites often sit behind WAF; their PDFs are usually reachable from a search result link.
- Village halls on free site builders hide emails behind Cloudflare protection or contact forms:
  record the obfuscation, look for a phone that reaches a named role, else HOLD.
- Many clubs publish only a Facebook page: no official site, HOLD unless another source names a
  role-bound email.

## Roles (match on title, case-insensitive; groundsman = greenkeeper = groundskeeper)

| Class | Examples | Use |
|---|---|---|
| decision | head groundsman/groundskeeper, head of grounds, head of sports turf, head of estates/facilities, estates or facilities director | A when bound to a literal email |
| operational | grounds manager/supervisor, pitch or turf manager, grounds team leader/foreman, facilities/estates/site/maintenance manager, head caretaker | B when bound to a literal email |
| influencer | operations manager/director, venue/stadium/club/general manager | C route, rank below the two above |
| gatekeeper | bookings manager/officer, venue/pitch/ground/facility hire | C business entry point |
| excluded | finance, accounts, payroll, HR, marketing-only, sellers of equipment (competitors) | X |

## Grades (per contact route)

- **A**: decision role + literal public business email + strong binding (person, role and email in
  one block, P1).
- **B**: operational role + literal email + strong binding (P1, or role/department + email, P2).
- **C**: an official business entry bound to the account in a grounds / facilities / venue /
  bookings / operations context (e.g. grounds@, bookings@, facilities team inbox).
- **D**: a real generic official inbox of the account (info@, office@, enquiries@). Count
  separately; never present as a key contact.
- **X**: excluded role or segment, insufficient or conflicting evidence, or no rule fits
  (`RULES_UNKNOWN`, e.g. a named procurement officer). Always state which.

A blocked or partially parsed page cannot support an A. Old documents (minutes, PDFs) need their
date; an old role is HOLD until current evidence confirms it.

## Member A's working rules

- The goal is valid key-role emails per organisation; an organisation may have several.
- A switchboard number is not a valid contact; a phone counts only when it reaches a named role.
- A name is not required if the role-bound email is real.
- Do not infer someone's responsibility from the shape of an email address; never guess formats.
- Every organisation gets a row, including `Not found` with the reason and pages tried.
- A sample batch is not the pool, and the first pass is not completion: report coverage honestly.
- Source method test: try 30 accounts; account rate ≥10% expand, 5–10% optimise once, <5% stop
  that method. Use only when the user sets these thresholds for the task.
- Link-driven discovery (following real site links) found emails at 62–88% of sites in past GB
  work; guessed paths found 6.5%. Follow links.
