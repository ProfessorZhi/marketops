---
name: marketops-knowledge
description: Business judgment for any MarketOps sales-research job, distilled from Bruce's research lines and the plugin's own runs - what counts as success, how to grade a contact (A/B/C/D/X, the two ledgers), who the customer is, which kinds of lists pay (timetables and yearbooks name people; registers give names; directories are never websites), how to pilot, scale or stop a source, the questions to settle before starting, and the blockers that cost most (idle hands, no end, pages with no address). Use it when writing a brief, choosing, registering or dropping a source, judging whose an address is or what grade it deserves, deciding whether to keep going, or whenever the A/B rate looks thin or wrong - for any market or product.
---

# Business judgment for research

Distilled from Bruce's research lines across markets and products. Only what holds for any
market or product is kept here; what one line or market taught (its sources, titles,
corrections, numbers) lives in `references/projects/<line>.md` - read the closest one when you
write a brief. Where Bruce never decided something, this page says so: ask the user, never fill
the gap with a guess.

## Success

**Research-valid work addresses per unit of time** - not organisations finished, not coverage,
not pages read. Fifty organisations finished with no strict contact is not success. What costs
most is not crawling but the upstream choice of **which list and which kind of page to look
for people on**; when yield is low, change the source or the page type before tuning anything
else.

## Grading a contact

Grades describe how close an address is to the people the product needs, tied to the evidence
on the page. The brief puts the product's own functions and titles into them.

| Grade | What it is | Evidence required |
|---|---|---|
| A | a named key person: decides, evaluates technically, buys, or runs the operation the product serves | name + role + organisation + address tied together on an official page |
| B | an operator role for that function (on sports-ground lines: grounds, facilities, estates, maintenance, operations) | role + real work address in the same official record; **a name is not required** |
| C | that function's department inbox (grounds@, facilities@, booking@ ...) | official, for this organisation |
| D | a general official inbox (info@, office@, enquiries@) | official; labelled D, never presented as a person |
| X | functions unrelated to the product's use or purchase (admissions, HR, finance, marketing, PR, student or customer service, no-reply), anything inferred, guessed or malformed | - |
| review | the owner is clear, the relevance is not | kept with the reason, outside the grades |

A name is the condition for A, not for B: a role with a real work address is a route.

**Two ledgers, never one number**: research-valid routes (A-D, each labelled) and the strict
ledger (named A, A, or A+B - `brief.strict_ledger`, default A+B). The workbook reports both. They answer different questions;
passing one off as the other overstates a run.

**Never guess an address** - no pattern, no concatenation, no probing. A named person with no
address on any page: one or two targeted searches, then record that none was found; never put a
general inbox in their place.

## The customer

- **Whoever owns, runs or maintains what the product is used on or for** - the operator, the
  public body or department, the group, the maintenance contractor. Competitors and resellers
  are kept, marked, not researched; end customers and dealers are separate jobs.
- **Test a role by what the person does, not the title**: approves budget or suppliers,
  evaluates the technology, owns the work or the equipment, controls purchasing, or can credibly
  take a demo - any one is enough. Titles differ by market and product; take them from the page.
- **The organisation researched may not be the buyer** (a tenant using a site someone else owns
  and a contractor maintains): record the route under the organisation it reaches. One site or
  account often needs several people.
- **Count each organisation once**, at account level - never by contact rows.
- **A list the client hands over is trusted**: dedupe it, drop history and hard exclusions, do
  not re-screen it for relevance - the client has already decided who is in scope.

## Running a job

- **Building the pool and researching people are separate jobs**; a pool's unit is a channel
  (one register, one directory, one export), not a page.
- **Calibrate before scaling**: a small batch (about 20-25 organisations) to check the method,
  then hundreds.
- **Test each source on about 30 organisations** by its strict-account rate: >= 10% scale it;
  5-10% improve once, do not scale; < 5% stop that method and change source. A source is judged
  by what its organisations yield, not by its size.
- **Stopping**: one low batch is never a reason to stop; repeated low waves stop the method or
  the source, never the job - work out why, then change.
- **Page budget**: about 6-8 high-value pages per organisation, continuing only while pages still
  bring new addresses; without a budget an organisation with no public address eats unlimited
  time.
- **Hand over on disk, automatically**: a handover that lives in a chat message is not one.

## Settle before starting

Bruce's lines answered these differently - there is no default; ask the user once, up front:
- what is sold and the one application scenario (several products sold together are one list);
- which grades the strict ledger counts, and whether D counts as valid (he never defined it);
- the unit: the operating organisation, the site, or the account;
- whether the pool is capped once deep research starts, or keeps growing;
- in-house or outsourced operation (outsourced: the contractor is the customer);
- what the client already has (lists, past replies, exclusions) and the delivery columns.

## Where whole lists come from

By yield, an order of magnitude apart: one request that returns the whole roster (open-data
export, register, federation or association finder, CMS endpoint) > content sitemaps > the
home page's real links > guessed paths. A list from the client beats every automatic path. A
register is not a directory of customers: read 20 rows before pooling it. Maps give places, not
organisations. Search engines return pages, not rosters. Pages by yield: staff, team and board
pages carry many times more A/B than contact or home pages; the legal page names the operator;
department pages beat searching role titles.

## What sources taught (any market)

Each lesson came from a run; none names one market.
- **The users of a public facility are on its booking and timetable files** (slot schedules,
  allocation tables, a sports office's yearbook PDF): one file names dozens of organisations
  with their president or secretary and a mailbox - the best A/B per page of any source.
- **Rank lists by whether they print a name and a role, not by their size**: on all of Paris a
  sports office's club list gave A/B for every organisation worked, a federation committee's two in
  three, while the city's slot table - 81% of the pool - gave about one in fifty. A big list of
  names is pooled for coverage and duplicates; the people come from the small lists.
- **A company or association register gives names, not people**: hundreds of rows, few with any
  route (079: 1.7% A/B on 225). Pool it for the registry numbers and the duplicates it settles,
  let its pilot decide, and do not let its size steer the team.
- **People first, numbers beside them**: the lists that name people are what the A/B rate is made
  of - mine them first. A list with a registry number (RNA, SIRET, UAI...) is worth mining early
  too, by a miner of its own, because later lists then merge on the number instead of a guess at
  names - but it rides beside the people lists, never ahead of them or instead of them.
- **Directories, phonebooks, acronym dictionaries and listing sites are never an organisation's
  website**, even when their page title carries its name.
- **A portal's own inbox in the footer of every fiche is the portal's** - not each organisation's.
- **Addresses hide**: `name (at) club . fr`, `name[at]club.fr`, ROT13, `mailto:` links, Markdown
  escapes - read the page with the tools before deciding it has none.
- **A page read before its script ran shows only its frame**: an empty big page is read again with
  another reader, never taken as "no address".

## The blockers that cost most

- **Pages with no address at all** - the usual bottleneck, not speed. Change page type or source.
- **One list, one kind of entity**: an organisation list has no sites, a site census has no
  tenants. Pool from several sources, every layer the job needs.
- **Duplicates** across channels and inside one site (copies of the same page): a pool that
  shrinks by most of its size after dedupe was counted wrong all along.
- **Green but empty**: a status says done while the rows are empty. Judge by rows on disk.
- **The middle layer never used up**: rows built in one step and never reaching the deliverable.
- **Splitting work across too many hands**: coordination, not crawling, became the cost - one
  agent owns a package end to end.
- **Extraction before blame**: lost `mailto:` links, undecoded addresses, compressed responses,
  DNS bursts that drop live sites. Zero addresses on a big site is a parsing problem first.
- **Idle hands**: a worker that ended its turn, or waited on an empty lane while the pool could
  grow, cost more than any slow site (079: 26 minutes). Keep claiming; when the lanes are empty,
  find more lists.
- **No end**: a run without a time box dilutes its own rate - the late hours read the weakest
  sources.
- **Asking one question at a time**: open questions held his queue for a hundred rounds. One
  numbered list, one reply.
