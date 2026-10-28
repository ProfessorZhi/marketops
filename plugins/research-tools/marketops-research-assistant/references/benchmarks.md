
# The quick test: one Paris district from zero

For every plugin change, before the long tests. Small enough to run in about an hour for a few
euros, real enough to exercise the whole chain.

- **Brief**: `{"goal": "people who run, manage or decide on indoor sports venues - venue and
  facilities managers, sports department heads, club presidents", "country": "FR", "language":
  "fr", "region": "Paris 11e arrondissement (75011)", "scenario": "indoor sports venues of one
  district", "address_rules": {"info": "X", "admin": "X", "book": "X"}, "strict_ledger": "AB",
  "fresh": true}`. Nothing else: no sources, no pool, no playbook - the finders find them.
- **Start from zero**: a new project name per run (`bench-paris11-<version>-<date>`) and an empty
  `MARKETOPS_HOME` (no shared pages, searches or lessons). `project()` must show no brought-in
  source; one is a failed test, not a result.
- **Team**: the lead plus a finder, a miner, a websites worker and two researchers, all at once.
  Record the model every worker really ran on.
- **Limits**: 90 minutes, about 30 EUR of provider spend, paid TinyFish Agent off.
- **Report**: sources found / pooled / piloted / scaled / dropped; organisations (and the share
  out of scope); organisations researched per source; organisations with A/B and A/B per 30
  minutes; X share; spend per provider; agent turns; the slowest stage and the most frequent
  error from `calls.jsonl`.
- **Pass**: at least 3 sources each researched on its pilot; no source's organisations left
  unread while another's are past their pilot; every contact quoted; spot check of 30 rows >= 90%.

# Two standing tests

Every plugin version runs both. Report the version (`project()` shows it) with every number.

| | Bruce: France natural-grass pitches | Paris indoor sports venues |
|---|---|---|
| Measures | quality and breadth against a reference | speed, scale and the client's delivery |
| Input | Bruce's France family: 1,874 organisations by `account_id`, 249 of them graded by him (A/B/C/D = 4/67/102/76) | the city's venue register plus clubs, schools, private venues the run finds |
| Grades | Bruce's contract (info@ is D) | the client's: info@ / admin@ / book@ are X (`address_rules`) |
| Reference | share with A+B on his 249: 71/249 = 28.5% | the brief's goals (share with A/B, named A/B) |
| Pass | share with A+B >= 37% (1.3x), A count >= his, precision >= 90% on a 100-row page check | the brief's goals; no stage idle; cost per organisation reported |
| Effort | report both sides as turns and human messages; time only as context | the plugin's minutes, organisations per minute |

**Effort is measured in turns and human messages, not hours.** Bruce's France line spans
740.9 hours first-to-last message, but its agent turns add up to 4.13 hours (333 turns over 10
threads) with 422 user messages - the two readings differ 180x, so "time <= 1/5 of Bruce" has
no single meaning. Report: plugin minutes, turns, human interventions; Bruce: turns, turn
hours, user messages, span.

Bruce's graded accounts stay on the machine that holds his corpus and are never given to
the run. Compare per account afterwards: both found / only Bruce / only the plugin / neither;
for every "only Bruce", write the general defect and how to reproduce it.

## What to report

1. Share of organisations with A+B (headline), A+B routes per 100 organisations (all, and at
   most 3 per organisation), named A/B, coverage (any of A-D).
2. Organisations per minute, minutes, cost and cost per organisation.
3. From "过程（耗时）": seconds per organisation per stage, the stage where first A/Bs came
   from, per source.
4. From "数据源" and "贡献（消融）": what each source and stage added that nothing else did.
5. Human interventions, and what they were.

## Finding what slows or loses

- **A stage taking most of the time** with few first A/Bs: test it with `brief.ablate` on
  the same project (the library makes the rerun almost free) and report both runs.
- **Many X or D on one domain**: a portal or shared inbox being read as an organisation's own
  site - check `crawl.csv` notes and the shared-portal handling.
- **A+B rows marked review**: read the reasons; a reason that hides real A/B is a defect.
- **Organisations per minute falling as the project grows**: something scans everything per
  organisation; report it with the stage times.
- **A source pooled late finishing last**: sources should take turns; report if not.
- **Count records, not lines**: in a transcript a CSV arrives inside a code patch with escaped
  newlines and the same tool output repeats across messages - a count of "accounts" from a
  transcript was several times too high once (12,927 claimed, 1,874 real). Split into real rows and de-duplicate first.

Only general fixes: never tune the plugin to one test's names or pages. After each test, run
the `learn` command so the project's knowledge file gets the new benchmark row.
