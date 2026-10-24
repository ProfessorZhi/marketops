---
name: marketops-directories
description: Turn a saved annuaire, club finder or committee page into organisation, person and email candidates in one local call. Use when a page prints many records, when an excerpt would need dozens of calls, or when a directory is the only source of named contacts.
---

# One page, many records: read it once

A town's sports annuaire prints 135 clubs, each with a name, a contact person
and an address. The excerpt tool is built for evidence snippets, so reading that
page through it costs thirty calls and still leaves you with prose, not records.
`marketops_directory_extract` reads the whole saved page once.

## The flow

1. Fetch the page and keep its artifact ID:
   `marketops_research_step(run_id, operation_id, task_kind='known_public_url', url=...)`.
   Paginated annuaires are separate pages: one artifact per page.
2. `marketops_directory_extract(run_id, artifact_id=<page>)` — it returns
   `records`, `with_email`, `with_person`, `without_organization`, `warnings`,
   an extracted JSON `artifact_id`, and `items` (2–3 per record: organisation,
   person, email).
3. Read the next page with `offset=reply['next_offset']` until it is null.
4. Stage what you need: `stage=true` on each call, or pass `items` to
   `marketops_propose_batch` (20 per call). The candidates quote the page, so
   grading sees the person's name and the address in the same block.
5. Deliver with `marketops_handoff`. The extracted JSON stays in the Run as
   evidence of what the page said.

## What it reads

- Any label followed by a colon: `Nom de la personne à contacter :`,
  `Adresse mail :`, `Téléphone :`, `Site internet :`, `Adresse :`,
  `Discipline(s) pratiquée(s) :`, `Équipement(s) utilisé(s) :`, and their
  English/German equivalents. Labels are matched in French, English and German;
  a new market's wording belongs in `scripts/research_extract.py`'s `_LABEL_GROUPS`.
- The organisation name is the words printed immediately before the first label
  of a block (a trailing abbreviation in brackets is dropped). A record with no
  printed name is kept in the JSON but never staged under an invented one.

## Honest limits

- `warnings` says what the page defeated: `DIRECTORY_NO_REPEATING_LABEL` (not a
  record list), `DIRECTORY_LAYOUT_NOT_RECOGNISED` (labels repeat but no name is
  printed before them), `DIRECTORY_TRUNCATED` (more than 5,000 records).
  Report the shortfall; do not fill it with guesses.
- A record needs a printed contact to be staged, so a page of names without
  emails yields organisations, not routes.
- `records` may be lower than the page's own count: a single-word club name or
  a name broken by a layout quirk may not be recognised. Say so in delivery
  notes rather than lowering the target silently.

## When the page is not a directory

- An API or open-data export: `marketops_pool_import` (artifact mode) is the
  right tool; do not parse it with this one.
- A page behind a challenge: it stays unopened. Search the domain and use the
  result titles as leads, or use another list.
- One organisation's own site: `marketops_site_harvest`, which follows its pages.

## Why this is shipped

The same blocker — "the page is a list, excerpts cannot carry it" — produced an
ad-hoc scraper in the Paris run. A blocker that recurs belongs inside the
plugin, where it keeps the evidence chain intact and costs no provider calls.
When you meet a new recurring blocker, record it with `marketops_note` and
raise it as a capability, not as a reason to leave the tools.
