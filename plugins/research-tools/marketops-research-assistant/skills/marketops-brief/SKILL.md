---
name: marketops-brief
description: Write or correct the brief of a MarketOps research project - who counts as a customer, what A/B/C/D/X mean for this market, the local job titles, what to look for and where, and the client's delivery rules - using the definitions Bruce had to settle in his projects. Use when starting a research project, when the user redefines a grade or scope, or when results look wrong.
---

# The brief

`project(name, brief={...})`. Ask the user once, up front, for what is missing. In Bruce's
sessions most of his interventions (45 of 78) were definitions, not bugs - "what counts as a
customer", "what counts as valid", "which number is true". Settling them in the brief saves
whole reruns.

```json
{
  "goal": "people who look after natural-grass pitches (mowing, line marking)",
  "region": "France",
  "country": "FR",
  "language": "fr",
  "scenario": "natural-grass line marking",
  "routes_per_org": 3,
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

Before writing it, read the matching file in `references/projects/` (same market or same
kind of organisation): its titles, sources and corrections are where to start.

`country` (ISO-2) and `language` steer every search engine - without them they answer as for the
US in English.

Bruce's own one-line definition of who to find, worth reusing as the `goal`: the people who
**would want to see a demo of the product, can recommend it, or can decide on it**.

`scenario` is written on every delivered row next to country and region - Bruce asked for
"国家、洲、终端的明确提示" on every list, and a row whose country is unknown is not delivered as
unknown: fix it. `routes_per_org` is how many A/B routes the dig looks for per organisation
before moving on (default 3; Bruce: "1个账户按3-5个关键角色邮箱来, 最低不能低于1个, 不能硬3-5个降低
效率和质量"). Small organisations: one route is enough - hand it over and move on.

## Questions to settle (each one cost Bruce a rerun when left open)

1. **What the client sells, and the application scenario** - one country, one scenario per
   project (mowing and line marking were run as separate lines).
2. **Customer or competitor**: whoever buys and uses the product to do the work - venue owner,
   operator, grounds team, the maintenance contractor - is a customer. Whoever sells or
   distributes the same products is a competitor: keep the row, mark it, do not research it.
3. **Who maintains the ground**: in-house or outsourced. When it is outsourced the contractor
   is the customer to reach (EU tenders gave 222 accounts with proof of outsourcing).
4. **The unit**: the operating organisation, researched once with all its venues; a person's
   name is not required for a route to count (a role mailbox can be B).
5. **What the client already has**: lists, registers, past replies. A role priority list
   built from real replies replaced Bruce's own guesses in one project - ask for it.
6. **Delivery rules**: inboxes the client excludes, the columns they want.
7. **Which scenario ranks the titles**: title priority is per scenario, not universal - "do
   not default to the highest title; prefer whoever actually runs the turf, grounds, equipment
   or the outsourced maintenance". Golf ball-picker robot: Range Manager -> Director of Golf ->
   Golf Operations -> GM, the Superintendent not first; ATX line: the Equipment Manager ranks
   much higher. Ask which product the list is for before ranking.
8. **Scope by name**: outdoor sports grounds include artificial turf (natural and artificial
   both count from 2026-09-21; indoor sites do not); schools, universities and municipal
   grounds are sports grounds; golf is its own line; distributors are dropped.
9. **The researched organisation may not be the buyer**: a club may play on a council pitch
   maintained by a contractor. Record whom the route reaches; do not assume the club buys.

## Settled answers (do not reopen them)

- A public mailbox on the organisation's own site is a valid route: it forwards and it gets
  replies ("不用死磕一定要拿到非常强的 P0P1"). D is delivered and can be sent to; only X is not a
  valid address. Never invent or guess an address.
- A name is not required: a clear role + a real work address is enough.
- A role read from the organisation's structure is an assumption, not evidence - keep the
  title empty unless the page shows it.
- The output is called **研究有效邮箱** (researched valid addresses), never "可发邮箱" or "sendable
  routes": research and sending are separate, this plugin does not read send history or judge
  sendability.
- A historical list is a seed, not proof of coverage.

## Grades (Bruce's contract `fjd-email-role-qualification`, the default)

| Grade | Meaning |
|---|---|
| A | a named person who decides on or owns the grounds or the purchase: head of grounds / facilities / estates, sports department head, club president, the board member for the ground; their own address |
| B | someone in leadership or operations who can act on or forward it - groundsman, site / venue / operations manager, caretaker, infrastructure lead, other board members, club secretary - or a role mailbox for that function |
| C | an official team entry: bookings, sports office, department inbox |
| D | a generic official inbox (info@, contact@, office@) - a usable first contact, not a decision maker |
| X | not a route for this organisation: sponsor, web designer, no-reply, unrelated private |

- **A+B is the KPI, C/D are assets, X is waste - count them apart.** One rule letting general
  inboxes into the main table turned 78.8% of the output into D.
- **Visibility before size**: rank organisations by how likely they publish a role address,
  not by how big or famous the venue is (Bruce corrected the objective once for this).
- **A switchboard is not a contact** unless the page says it reaches the person.
- **Client delivery rules beat the defaults**: excluded inboxes go in `address_rules` (graded
  on arrival, no Jev call, marked `graded_by=rule`, still delivered for review). Without a rule
  a plain general inbox is D and role-ish addresses (secretariat@, sports@, service.*) go to
  Jev - they can be B or C.
- **Public mail domains are never excluded wholesale** - judge the local part. A wholesale
  free-mail filter once deleted 4,586 rows where 12 were wrong.
- **Job titles come from the page**: in one table 69.3% of the titles were a template value,
  188 were real. A row with no title on its page keeps an empty title.

## The market's own words

Use the local titles in `grades` and `focus.search_terms`. Starting points - the words marked
* appeared in Bruce's own searches (`derived/websearch-queries.csv`) or target lists; confirm the rest on the project's pages:

| Market | Decides / owns (A) | Runs or forwards (B) | Team inbox (C) |
|---|---|---|---|
| France | président*, directeur / responsable du service des sports, responsable des espaces verts | secrétaire, régisseur des installations, gardien de stade, adjoint technique | service des sports, réservations |
| UK | head of grounds*, facilities* / estates manager, club chairman, school business manager | groundsman, club secretary, bursar, site manager | bookings / lettings, parks team |
| Germany / Switzerland | Präsident / Vorsitzender, Leiter Sportamt | Platzwart, Ansprechpartner*, Vorstand Infrastruktur, Sekretär | Sportamt, Vermietung |
| US | athletic director*, owner* / founder* / president* (contractors, clubs), parks & recreation director | field / grounds* manager, league president | facility rentals, parks office |

## Corrections are new facts

When the user redefines a grade, scope or rule: update the brief, `grade(regrade=True)`,
keep going - and add the correction to the project's file in `references/projects/` with the
`learn` command, so the next project starts from it.
