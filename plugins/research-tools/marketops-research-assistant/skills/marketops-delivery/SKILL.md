---
name: marketops-delivery
description: Close a MarketOps Run - dispositions for every target, count reconciliation, independent sample check, one handoff to the audited XLSX, and an honest report. Use when research is done or the budget is spent.
---

# Close, check, deliver

The only way to produce the workbook is `marketops_handoff`. Never write XLSX/CSV yourself, never
read the databases or plugin code; the handoff applies the evidence gate you would otherwise skip.

1. **Every target has a disposition.** Admitted routes, or a review record. A missing contact is a
   HOLD, not an omitted row:

<!-- tested-example:target-hold-review -->
```json
{"run_id":"run-example","items":[
  {"record_id":"review-example-hold","stage":"review","kind":"review","entity_key":"review-org-example","facts":{"review_type":"target_disposition","target_kind":"organization","target_entity_key":"org-example","disposition":"HOLD","reason":"The saved organization source does not establish a role-bound public contact; approved research is exhausted."},"artifact_ids":[]}
]}
```

   The four dispositions: `RESEARCHED_CANDIDATE` (researched, routes recorded), `HOLD` (unresolved,
   give the reason), `EXCLUDED` (needs an `exclusion` quote), `DUPLICATE` (names the kept record).
   No other value exists. Correct a review with a new `record_id` and `facts.supersedes`.

2. **Reconcile.** For each source: listed = saved + excluded + HOLD. Any gap means rows were lost;
   find them before delivering.
3. **Check.** `marketops_evidence_audit(run_id)`; on hosts with subagents, run `research-verifier`
   on a sample (all A/B rows if fewer than 20). Fix or HOLD what fails.
4. **Hand off once.** `marketops_handoff(run_id, operation_id)` writes the nine-sheet XLSX, CSVs,
   manifest, client-view files (business rows only) and the experience draft. A `PARTIAL_HANDOFF_*`
   status is reported as partial, never as clean. Do not start new discovery just to avoid a
   partial result.
5. **Report: lead with the goal.** The first line says whether the user's goal was met, against
   the brief: for example "Goal: contacts for 30 venues. Met for 7, not met for 23". Use the
   handoff's `goal_satisfied`, not `delivery_completeness`. COMPLETE only means the paperwork is
   in order, so never write "complete" when the goal was not met. Also say whether Crawl4AI ran
   the whole time, and whether pages were read by TinyFish only (which the user agreed to).
   Then, in plain terms: targets covered; admitted routes by grade; generic inboxes counted
   separately; HOLD/EXCLUDED with reasons; sources blocked; what the user should review. Workbook
   rows are candidates for human review, not approved contacts, and row counts are not market
   coverage.
6. **Money**, from the handoff's `run_cost` and `marketops_costs(action='balances')`: a small
   table per provider (calls, billed units, estimated USD), the total, what each account has left,
   which provider ran out or was replaced and when, and any provider not used with the reason.
   Say which figures are estimates; unknown stays unknown, never 0.
7. End with `marketops_feedback_pack` (see `marketops-feedback`).

Editing a client template: keep the original, diff before writing, never delete HOLD/UNKNOWN rows
and never promote a candidate with a formula.

Afterwards: one `marketops_note` per notable incident (blocked source, provider switch, user
correction) and `marketops_experience_feedback` for lessons you actually used.
