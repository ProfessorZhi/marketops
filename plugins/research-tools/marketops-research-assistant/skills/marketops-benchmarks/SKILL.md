---
name: marketops-benchmarks
description: The two standing tests for every MarketOps plugin version - Bruce's France natural-grass pitches (scored against his own graded accounts) and Paris indoor sports venues (a client delivery requirement) - with the metrics to report and how to find what slows a run or loses results. Use when testing a plugin version or comparing runs.
---

# Two standing tests

Every plugin version runs both. Report the version (`project()` shows it) with every number.

| | Bruce: France natural-grass pitches | Paris indoor sports venues |
|---|---|---|
| Measures | quality and breadth against a reference | speed, scale and the client's delivery |
| Input | Bruce's France family: 1,874 organisations by `account_id`, 249 of them graded by him (A/B/C/D = 4/67/102/76) | the city's venue register plus clubs, schools, private venues the run finds |
| Grades | Bruce's contract (info@ is D) | the client's: info@ / admin@ / book@ are X (`address_rules`) |
| Reference | share with A+B on his 249: 71/249 = 28.5% | the brief's goals (share with A/B, named A/B) |
| Pass | share with A+B >= 37% (1.3x), A count >= his, precision >= 90% on a 100-row page check | the brief's goals; no stage idle; cost per organisation reported |

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

Only general fixes: never tune the plugin to one test's names or pages.
