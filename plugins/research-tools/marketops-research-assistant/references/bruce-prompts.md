# Bruce's prompts, adapted to this plugin

Bruce re-gave the same rules 3-9 times across sessions because they lived in chat, not in a
file the next thread loaded (`docs/benchmarks/bruce-knowledge/PROMPTS.md`). Here each of his
templates is mapped to where the plugin keeps it, so it is written once.

## Kickoff -> the brief

His kickoff: one scenario, one country, one stage; say in one sentence how you read the task
(a wrong scenario or country voids the whole run); countries in a fixed order, each frozen
before the next; start at once, no second confirmation.

```text
Research {{scenario}} in {{country}} with marketops.
Customer: whoever buys and uses {{product}} to do the work - {{owner / operator / grounds team /
maintenance contractor}}. Not: {{competitors, distributors, golf / indoor}}.
Find: the people who would want a demo, can recommend it, or can decide on it; {{3}} routes per
organisation, at least 1.
Titles in order: {{scenario-specific list}}.
Sources I already have: {{files / registers}}. Client excludes: {{info@, ...}}.
Start at once; put every open decision in one numbered list.
```

The agent's first line repeats the scenario and country it understood. Everything above goes
into `project(name, brief={...})` (see marketops-brief) - `scenario`, `country`, `language`,
`routes_per_org`, `exclude`, `grades`, `address_rules`.

## Task brief -> the run's defaults

| He wrote | The plugin |
|---|---|
| batches of ~50 accounts, write each one on finishing, resume, never redo | the run writes every organisation as it finishes and resumes from disk; the library makes reruns nearly free |
| 4-6 high-signal page types, at most 6-8 pages, never the whole site; 12-24 concurrent; 15-20 s timeout; <=5 MB | people / contact / legal pages first, two levels, a 150 s budget per site; 24 workers |
| every row carries the country; titles in English and the local language | 国家/地区/场景 on every row; `focus.search_terms` in the local language |
| do not walk a checklist mechanically; do not trade truth for numbers; do not stop at the first contact | quotes and pages on every row; `routes_per_org` |
| pilot on 25-50 organisations, then scale | sources take turns: after 30 organisations each source's A/B share decides its turns |
| rules change in the shared source, with a version | the brief, `references/projects/`, the `learn` command |

## Handoff -> files, not messages

He wanted each stage to hand over by data file, never by chat, and a new machine to restore from
a manifest with a receipt. The project folder is the handoff: `brief.json`, the pools,
`contacts/`, `trace.jsonl`, the workbook. Another session opens it with `project(name)`.

## Acceptance -> what to report

```text
Done means: blockers 100%; key gaps may ship but are named.
Report conclusions only: sources used, current vs expected size, files written (absolute
paths), what blocked access.
Never "the code has been created" - give files, paths, counts.
```

From the plugin that is: the workbook path, the 对账 line, breadth (share of organisations with
A+B), A+B routes per 100 organisations, `ab_routes_per_30_min`, the sources and their yield,
the coverage gaps, and `needs_you`.

## Stop line

- A problem that will not resolve: record it, skip it, keep the rest running, report it once
  - not a stop for the whole run, and not a question every few minutes.
- A discussion is not an instruction: while the user is discussing, change nothing.
- Read-only tasks stay read-only; source files stay byte-for-byte.
- Stop or fix only the stage that is broken; stable stages keep running.
