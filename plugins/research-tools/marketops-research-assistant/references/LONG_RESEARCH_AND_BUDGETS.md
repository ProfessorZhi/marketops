# Long research and monetary budgets

The research engine supports many bounded tool calls on the same durable Run
and pool. A batch size, tool deadline, context rollover or saved workbook is not
a completion quota. Contact work prioritizes different decision roles; it does
not invent a required number of people per facility.

## Red lines (default) and the older envelope

After reading the account balances, local setup opts into money mode with
`research_runtime_setup.py configure-money --confirm`. Flags:

- `--spend-fraction 0.90` (default): research may use 90% of each observed balance;
  the remaining 10% is that account's **red line**.
- `--money-model RED_LINE` (default): spending is checked against the freshest
  balance reading. A refreshed or topped-up balance is usable at once, down to the
  red line. `ENVELOPE` keeps the older frozen share of the balance at setup.
- `--total-usd AMOUNT`: the total red line across all accounts. The default is $500,
  a safety stop.
- `--provider-usd PROVIDER=AMOUNT`: a lower supplier ceiling, repeatable.
- `--renew-budget`: explicit renewal of the policy. It never clears the ledger.

During a Run the user decides how much to keep:
- `marketops_costs(action='set_red_line', provider, keep, user_words)` moves an
  account's red line.
- Raising it is always allowed.
- Lowering it (spending more) needs the user's own words. They are kept in
  `state/money-policy-history.jsonl` with the old and new values.
- Unknown or stale balances still authorize no charged call.

Money and requests are separate metrics. Crawl4AI local pages and reviewed free
TinyFish search/fetch consume no monetary budget. Their operation/page/time
metrics remain recorded; browser timeouts, concurrency, rate limits, egress and
source refusal rules remain enforced. The historical request ledger is retained.
Only legacy request-count mode continues to enforce its request ceilings.

The existing authoritative ledger checks money/credit reservations atomically.
Timeouts and lost replies keep their exposure. Changing conversations, processes
or Runs cannot replenish anything. Under the red-line model a newer account reading
is the balance; held and unknown charges since it still count. Under the envelope
model a newer reading can only tighten.

Balances read automatically: SerpApi (Account API), Serper
(`google.serper.dev/account`) and the TinyFish wallet. Jev publishes no balance API;
enter the console figure once and usage is computed from tokens. Serper credits use
their conservative credit-value equivalent, separately from cash invoices. A free SerpApi allowance is bounded in vendor searches. Jev uses
the [official input-token rate](https://typesafe.ai/), $0.042 per million with
free output, and records derived charges from SDK-reported input tokens. Its
reservation is deliberately larger than a bounded text judgment; missing usage
keeps the reservation instead of inventing a zero charge. TinyFish paid Agent
remains disabled in the research profile; setting a wallet budget does not
enable a paid capability or grant target-site access.

`marketops_costs(action='budget')` reads each account's balance, red line and the
amount usable above it. `action='set_budget'` narrows; `action='set_red_line'`
moves a red line as above. Account readings can be stale and other apps
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
