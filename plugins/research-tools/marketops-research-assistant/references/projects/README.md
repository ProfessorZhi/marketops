# Project knowledge

One file per business line - what was researched, where the organisations and people were
found, the titles that counted, what went wrong and the numbers behind it. The brief and
sources skills read the matching file before a project starts; the `learn` command adds to
it when a project ends. New markets and new lines get a new file in the same shape.

| File | Line | From |
|---|---|---|
| [fr-natural-grass.md](fr-natural-grass.md) | France - natural-grass pitches (mowing, line marking) | Bruce's sessions |
| [uk-pitches-schools.md](uk-pitches-schools.md) | UK - pitches and school grounds | Bruce's sessions |
| [us-sports-fields.md](us-sports-fields.md) | US - sports fields, schools, contractors | Bruce's sessions |
| [de-sports-grounds.md](de-sports-grounds.md) | Germany - sports grounds and contractors | Bruce's sessions |
| [ch-natural-grass.md](ch-natural-grass.md) | Switzerland - natural-grass football pitches | Bruce's sessions |
| [global-golf.md](global-golf.md) | Golf clubs, many countries | Bruce's sessions |
| [other-lines.md](other-lines.md) | Parking, agriculture, schools for line-marking robots, Netherlands | Bruce's sessions |

Source of the Bruce numbers: the `marketops-session` analysis of his Codex and WorkBuddy
sessions (`derived/l2/*.md`, `docs/GOLDEN_PATTERNS.md`, `docs/FINAL-REPORT.md`). Its L2
summaries are LLM syntheses: treat single numbers as a guide and measure your own.

## Template for a new file

```markdown
# <Market> - <what is researched>

- Product / scenario: <what the client sells, the one application scenario>
- Unit: <organisation / venue / account>, merge key: <name + place, register id ...>
- Last updated: <date>, by: <project name(s)>

## Pool sources
| Source | What it gives | Organisations | With A/B | Note |

## Pages that paid
<page types and paths, with counts>

## Titles
| Grade | Titles seen (local language) | Count |

## Corrections
- <old rule> -> <new rule>, because <reason>

## Pitfalls
- <what went wrong, how to see it, what fixed it>

## Benchmarks
| Run | Organisations | With A/B | Named A/B | Minutes | Cost |
```

Never write the researched organisations' names, people's names, addresses, phone numbers
or account ids into these files: counts, titles, source names, page types and rules only.


## Rules that come from his own instructions

Extracted from 3,151 of his own messages across 168 sessions; the evidence rows (quote, locator,
count) are in `docs/benchmarks/bruce-knowledge/`. These apply to every line above.

- One thread, one job. Pool building, deep research, first touch and reply monitoring are four
  separate flows that hand off in order; an end-to-end thread was rejected because a fix in one
  stage then forced a rerun of all of them.
- Research never sends. "不自动发送开发信" is a thread rule; the send queue is built from the
  research output and shown once for confirmation before the first wave.
- A country batch freezes before the next starts; the order he used was UK -> FR -> DE ->
  IT/NL -> AU/NZ/IE/CA -> rest. Within a large country, states run as parallel sub-threads under
  one main agent.
- Measure a batch before scaling it: 20 pages -> check output -> 200 -> 500. A green dashboard is
  not a measurement (see the mirror pitfall in `fr-natural-grass.md`).
- The send window is 14:00-22:00 local; outside it the queue waits. The owning thread gets a
  progress line every 15 minutes, including the send rate.
- A failed send is recorded and skipped, never retried inside the wave; after the wave the
  skipped ones are retried, and a third pass runs until the queue is empty.
- Reply monitoring runs in the same thread as the send, never a new thread. Its interval was
  changed 3 h -> 6 h -> 12 h as volume grew: treat it as a setting, not a constant.
- A generic mailbox is a route (it can forward); a D grade is sent to with a lower reply
  expectation; something explicitly identified as not a mailbox is not a route.
- The delivered row carries country, state/region and the terminal/scenario, so the sending
  thread can split the queue without a second lookup.
- Anything that needs his judgement goes into one numbered list in the owning thread and comes
  back in one reply, not one question at a time.
