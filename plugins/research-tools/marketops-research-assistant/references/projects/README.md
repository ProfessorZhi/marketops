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

He reversed himself more than once. Only the latest version of each rule is stated below, with
the date he said it; the superseded version is kept in
`docs/benchmarks/bruce-knowledge/CORRECTIONS.md` (its §三 lists the ones that still differ
between business lines and are left undecided).

- **One pipeline, end to end (2026-09-21).** Pool building, the deep dig and the mailbox decision
  run as one continuous flow, and the finished list is archived by grade: "就一套流程走完，四套流程乱且复杂，效率一定会低". The earlier instruction to split it into separate flows handed over 1 -> 2 -> 3 -> 4
  (2026-09-15 拆分; 2026-09-16/17 串行交接) is superseded — `CORRECTIONS.md` POOL-01.
- **Research never sends (2026-09-04, restated 2026-09-17).** "研究与发信完全分离" — no send history
  read, no sendability decision, no queue, no mail; the output is "研究有效邮箱", never "可发邮箱".
  The send side is a different job: see Outreach below.
- **A country batch freezes before the next starts.** The order he used was UK -> FR -> DE ->
  IT/NL -> AU/NZ/IE/CA -> rest. Within a large country, states run as parallel sub-threads under
  one main agent.
- **Measure a batch before scaling it** (2026-09-04: "每次按20页面下载为基础"): 20 pages -> check
  output -> 200 -> 500. A green dashboard is not a measurement (see the mirror pitfall in
  `fr-natural-grass.md`).
- **A generic mailbox is a route, not a person (2026-09-05).** A/B/C/D all count as research-valid
  routes, but D must stay labelled D: "必须标记为 D，不得伪装成场地负责人或具名联系人"; something
  explicitly identified as not a mailbox is not a route at all.
- **The delivered row carries country, region/state and the terminal scenario (2026-09-17,
  restated 2026-09-21 and 2026-09-22)**, so the sending side can split a queue without a second
  lookup; a country may not be left as "unidentified".
- **Anything that needs his judgement goes into one numbered list** in the owning thread and comes
  back in one reply, not one question at a time.

## Outreach (not this plugin)

The plugin researches; it does not send, does not monitor replies and does not build delivery
assets. The rules he stated for that side are recorded here only because they decide what a
research row has to carry at hand-over.

- **Send window 14:00-22:00 local**; outside it a row is registered and sent the next day.
- **Sending is automatic once commanded** (2026-09-01: "每次发送前，不要再和我确认要不要发送");
  an earlier confirmation gate is gone. But the French line states the opposite — "不自动发送开发信--严格执行"
  (2026-09-17) — so the corpus answers this per line, not globally; `CORRECTIONS.md` §三 keeps both.
- **A failed send is recorded and skipped**, never retried inside the wave; the skipped rows are
  re-run after the wave, and a third pass until the queue is empty (2026-08-18).
- **Reply monitoring runs in the same thread as the send, never a new thread** (2026-08-18).
  Its interval was set to 2 h (2026-08-18), 3 h (2026-08-21), 12 h (2026-08-23), 6 h (2026-08-25)
  and 12 h again (2026-09-16): treat it as a setting, not a constant. The 15-minute progress line
  that went with it was switched off on 2026-08-23.
- **A D grade may be sent** with a lower reply expectation (2026-09-08: "D可以去发信，回复概率低").
- **At most two execution threads at a time** (2026-09-02), one for research and one for sending.
