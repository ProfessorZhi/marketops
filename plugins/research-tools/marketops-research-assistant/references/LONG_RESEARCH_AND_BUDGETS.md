# Long research and monetary budgets

The research engine supports many bounded tool calls on the same durable Run
and pool. A batch size, tool deadline, context rollover or saved workbook is not
a completion quota. Contact work prioritizes different decision roles; it does
not invent a required number of people per facility.

## Owner-approved spending envelope

After reading the available account balances, local setup can opt into money
mode with `research_runtime_setup.py configure-money --confirm`. Optional flags:

- `--spend-fraction 0.30`: allocate at most 30% of each current observed balance;
  keep at least 70%. Unknown/stale balances do not authorize charged calls.
- `--total-usd AMOUNT`: a lower total monetary ceiling.
- `--provider-usd PROVIDER=AMOUNT`: a lower supplier ceiling, repeatable.
- `--renew-budget`: explicit renewal. Never use this to resume a Run or recover
  from a depleted envelope. Re-running configuration without it preserves the
  original allowance and all outstanding reservations.

Money and requests are separate metrics. Crawl4AI local pages and reviewed free
TinyFish search/fetch consume no monetary budget. Their operation/page/time
metrics remain recorded; browser timeouts, concurrency, rate limits, egress and
source refusal rules remain enforced. The historical request ledger is retained.
Only legacy request-count mode continues to enforce its request ceilings.

The existing authoritative ledger checks money/credit reservations atomically.
Timeouts and lost replies keep their exposure; changing conversations, processes
or Runs cannot replenish it. A newer account observation can tighten the original
reserve. It does not move the floor downward after spending.

Serper credits use their conservative credit-value equivalent, separately from
cash invoices. A free SerpApi allowance is bounded in vendor searches. Jev uses
the [official input-token rate](https://typesafe.ai/), $0.042 per million with
free output, and records derived charges from SDK-reported input tokens. Its
reservation is deliberately larger than a bounded text judgment; missing usage
keeps the reservation instead of inventing a zero charge. TinyFish paid Agent
remains disabled in the research profile; setting a wallet budget does not
enable a paid capability or grant target-site access.

`marketops_costs(action='budget')` reads the envelopes. `action='set_budget'`,
`amount_usd='...'` and optional `provider` can narrow an approved envelope; the
MCP cannot increase or renew it. Account readings can be stale and other apps
can spend outside this ledger, so the UI must label observations and estimates.

## Bulk discovery from saved data

Use `marketops_pool_import` with `run_id`, `artifact_id`, `pool_id`, `source_id`
and `preview=true` to inspect a SHA-checked saved JSON response. It supports a
JSON object containing `results`/`records`, a JSON list, or a browser JSON `<pre>`.

Then provide `mapping` and literal-value `filters`. For France Data ES, inspect
the actual fields first: `equip_nature` is the indoor/outdoor field, not the
presence of INTERIEUR in `equip_nom`; a facility name is commonly `inst_nom` and
its identifier `inst_numero`. Use an actual commune field, not the living-area
label `lib_bdv`. Record indoor eligibility as a source-backed claim. A mixed
padel directory is not proof that every listed venue is indoors.

Process local pages with `offset`/`limit`, following `next_offset`. These imports
make zero provider calls. `source_total_count` can exceed `saved_rows`; in that
case retrieve the next upstream API page, rather than claiming completeness.
Reconcile imported, duplicate and explicitly filtered rows for every source.
Keep facilities distinct from their operators and from individual equipment.
Reuse staff across facilities only with a sourced operator relationship.

## Contact coverage and delivery

Set `role_groups_wanted` in the brief to the desired subset of `management`,
`operations`, `commercial_events`. `marketops_next.contact_coverage` reports
missing groups. One person may cover more than one explicitly supported role;
there is no fixed headcount. Finding a generic inbox does not close named-person
research. After own-site harvesting, seek the missing roles in staff pages,
partnership/events documents, press releases and public PDFs. A weak source or
method should be replaced, not retried until the budget disappears.

Keep `usage_checkpoint.since` from `marketops_start`; pass it to
`marketops_costs(action='report', since=...)` and `marketops_handoff(since=...)`.
Report `session_cost` for this session and `run_cost` as cumulative, labelled
separately. The ledger preserves historical work and failed/unknown exposure.
