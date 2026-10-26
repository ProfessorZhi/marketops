---
name: marketops-brief
description: Write the brief for a MarketOps research project - who counts as a customer, what grades A/B/C/D/X mean for this market, the local job titles to search for, what to look for and where, and the client's delivery rules (e.g. inboxes they exclude). Use when starting or correcting a research project.
---

# The brief

`project(name, brief={...})`. Ask the user at most once for what is missing: what they sell,
region, kind of organisation, the roles wanted, what they exclude.

```json
{
  "goal": "people who look after natural-grass pitches (mowing, line marking)",
  "region": "France",
  "exclude": "golf courses; shops and distributors",
  "grades": {"A": "...", "B": "...", "C": "...", "D": "...", "X": "..."},
  "address_rules": {"info": "X", "admin": "X", "book": "X"},
  "focus": {
    "find": ["stade", "terrain de football", "terrain de rugby"],
    "areas": ["Lyon", "Villeurbanne", "Vénissieux"],
    "search_terms": ["responsable des espaces verts", "service des sports", "président"],
    "directory_terms": ["district de football clubs", "annuaire des associations sportives"]
  },
  "providers": {"search": {"serper": 0.5}},
  "ablate": []
}
```

## Who counts

- **Customer**: whoever buys and uses the product to do the work - venue owner, operator,
  grounds or facilities team, the maintenance contractor.
- **Competitor**: whoever sells or distributes the same products. Keep the row, mark it, do
  not research it.
- **One organisation, many venues**: the operating organisation is researched once (a city
  sports department running twenty pitches is one organisation with twenty venues).

## Grades (Bruce's contract `fjd-email-role-qualification`, the default)

| Grade | Meaning |
|---|---|
| A | a named person who decides on or owns the grounds or the purchase: head of grounds / facilities / estates, sports department head, club president, the board member for the ground; their own address |
| B | someone in leadership or operations who can act on or forward it - groundsman, site / venue / operations manager, caretaker, infrastructure lead, other board members, club secretary - or a role mailbox for that function |
| C | an official team entry: bookings, sports office, department inbox |
| D | a generic official inbox (info@, contact@, office@) - still a usable first contact |
| X | not a route for this organisation: sponsor, web designer, no-reply, unrelated private |

Omitted grades use these. A+B is the headline; C/D are delivered and count for coverage.
Always report them apart - one run turned 78.8% of its output into D and looked big.

**Client delivery rules beat the defaults.** When the client excludes inboxes (the Paris
brief: info@, admin@, book@ are X), write them as `address_rules`: those addresses are
graded on arrival, no Jev call, marked `graded_by=rule` (kept, can be reviewed). Without
such a rule a plain general inbox is D, and role-ish addresses (secretariat@, sports@,
service.*) go to Jev - they can be B or C.

## The market's own words

Grades and search terms must use the local titles. Starting points (refine from the
project's own pages and the marketops-sources skill):

| Market | Decides / owns (A) | Runs or forwards (B) | Team inbox (C) |
|---|---|---|---|
| France | président (club), directeur / responsable du service des sports, responsable des espaces verts, adjoint aux sports (commune) | secrétaire, trésorier, régisseur des installations, gardien de stade, chef d'équipe espaces verts, adjoint technique | service des sports, réservations |
| UK | head of grounds, estates / facilities manager, club chairman, school business manager | groundsman, head groundsperson, club secretary, bursar, site manager | bookings, lettings, parks team |
| Germany / Switzerland | Vorsitzender / Präsident, Leiter Sportamt, Leiter Liegenschaften | Platzwart, Hauswart, Greenkeeper, Vorstand Infrastruktur, Sekretär | Sportamt, Vermietung |
| US | athletic director, parks & recreation director, facilities director | field / turf manager, grounds supervisor, league president | facility rentals, parks office |

## Corrections are new facts

When the user redefines a grade, scope or rule: update the brief, `grade(regrade=True)`,
keep going. Never argue the old definition.
