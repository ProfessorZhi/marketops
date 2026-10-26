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
