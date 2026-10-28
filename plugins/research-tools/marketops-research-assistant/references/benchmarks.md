
# How a plugin change is tested

Four steps, each only after the one before is clean. The scale grows; the chain is the same.

| Step | Where, who | Scope | What it proves |
|---|---|---|---|
| 0 offline | the developer, every change | `tests/` (fake web): the agent chain, sources taking turns, each fix | nothing broke |
| 1 smoke | the developer, real providers, workers on a small model (Haiku) | one district, one segment, at most 20 organisations, from zero (below) | the real chain runs end to end: sources -> pools -> contacts -> export, every tool reachable, no worker stuck |
| 2 regional | the user's machine | Paris (the standing test below) | speed, yield and cost at a scale where problems show |
| 3 delivery | the user's machine | France | what the client receives |

Step 1 keeps a **baseline**: the same brief every time, the numbers in a table (version, date,
model, minutes, sources pooled / researched, organisations, with A/B, X share, tool calls,
errors, provider spend, agent turns). A change that lowers it is not shipped.

# The smoke test: one district, one segment, from zero

For every plugin change, before the regional test. Small enough to run in half an hour for a few
cents, real enough to exercise the whole chain: real lists, real sites, real graders. Pick a
district and a segment that exist there (the 073 smoke asked for climbing gyms in a district
that has none).

- **Brief**: `{"goal": "people who run, manage or decide on indoor sports venues - venue and
  facilities managers, sports department heads, club presidents", "country": "FR", "language":
  "fr", "region": "Paris 11e arrondissement (75011)", "scenario": "sports associations and clubs of
  one district (indoor: basketball, handball, volleyball, badminton, table tennis, gymnastics,
  combat sports, dance, fencing, climbing): presidents, secretaries, facility leads - at most 20
  organisations from at least 3 sources, no more than 8 from one", "pilot": 5, "address_rules": {"info": "X", "admin": "X", "book": "X"}, "strict_ledger": "AB",
  "fresh": true}`. Nothing else: no sources, no pool, no playbook - the finders find them.
- **Start from zero**: a new project name per run (`bench-paris11-<version>-<date>`) and an empty
  `MARKETOPS_HOME` (no shared pages, searches or lessons). `project()` must show no brought-in
  source; one is a failed test, not a result.
- **Team**: the lead plus two workers started at once in the background - sources+pool and
  research - all on Haiku (`claude -p --model haiku --plugin-dir <plugin> --max-turns 60`, host
  tools limited to the plugin's, Skill, Read, Glob, Grep and Agent). Record the model every
  worker really ran on.
- **Limits**: 35 minutes, a few cents of provider spend, paid TinyFish Agent off.
- **Report**: sources found / pooled / piloted / scaled / dropped; organisations (and the share
  out of scope); organisations researched per source; organisations with A/B and A/B per 30
  minutes; X share; spend per provider; agent turns; the slowest stage and the most frequent
  error from `calls.jsonl`.
- **Pass (the chain)**: at least 3 sources registered with their URL and their organisations
  filed under them; every source's organisations researched (none left unread while another's
  are past their pilot); site reads past the home page; every worker kept working until its
  lane ended; a journal entry from each worker; `RETRO.md` written; every contact quoted.
  A/B counts at this size are a baseline to compare, not a pass mark.

## Smoke baseline

| version (commit) | date | model | minutes | sources found / pooled / researched | orgs | site reads past home | orgs with A/B | rows A/B/C/D/X | tool calls / errors | provider spend | lead turns | journal | notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.7.3 (3622ca3), team of 7, 11e + 12e | 2026-10-28 | Haiku | ~20 (stopped) | 13 / 7 / 7 | 279 (cap 60 not kept) | 89 verdicts | 14 strict (16% of researched), 31 more A/B in review | 38/37/3/127/571 | 394 / 2 | ~$0.2 | ~970 model turns, all agents | every worker + lead retro | 8 agents active at once, all pools growing together; 390 directory addresses held from Jev; fixes in 24c6df9 |
| 0.7.3 (7bb8bc8), associations, 3+ sources | 2026-10-28 | Haiku | ~15 | 3 / 3 / 3 | 13 | 10 researched | 2 strict (15%; 20% of researched) | 1/1/0/6/7 | 76 / 0 | ~$0.06 | 18 | every worker + lead retro | every source researched on its pilot; no flood; waits <= 51 s; 3 organisations got a website late and were not swept; a wrong website the worker did not know how to clear |
| 0.7.3 (5b881a3), associations | 2026-10-28 | Haiku | ~25 | 4 / 1 / 1 | 20 | 15 of 18 read | **4 strict (20%)**, 5 named A | 5/0/2/18/134 | 70 / 0 | ~$0.10 | 20 | every worker + lead retro | one source filled the cap of 20; 123 X from one club fiche on its federation's site (fixed after); a 300 s wait outlived the host's 60 s tool timeout |
| 0.7.3 (3df6854) | 2026-10-28 | Haiku | ~10 | 4 / 3 / 3 | 17 | 12 of 17 | 5 as graded, **0 strict** (the 7 B were accueil@ / contact@ / info@ set by the worker, and two venue inboxes Jev sent to review; flagged since 170664e) | 0/7/3/12/1 | 55 / 0 | ~$0.13 | 12 | none (guidance came in af50018) | 38 of 55 calls without an agent name |
| 0.7.3 (921fe0f) | 2026-10-28 | Sonnet | ~8 | 3 / 3 / 1 | 15 | 0 of 15 | 0 | 0/0/0/11/5 | 49 / 0 | ~$0.03 | 15 | none | site reads stopped at the home page (fixed in 3df6854); maps rows filed under the agent |

A run whose brief is **not the one above** is not a row of this table, however well it went: its
numbers measure a different experiment. It is written up under `docs/benchmarks/<name>/` with the
deviation stated first, and the write-up says which baseline rows it may and may not be compared
with. `paris11-073-20261028` is one: its brief carried `max_per_source: 8`, so every source stopped
at eight organisations and the sweep lane ended with 299 organisations pooled but unread - by the
brief, not by the planner.

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
