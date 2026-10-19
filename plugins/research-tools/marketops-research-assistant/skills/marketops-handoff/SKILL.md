---
name: marketops-handoff
description: Check and transfer human-reviewed research-stage outputs between Codex, WorkBuddy and future MarketOps without treating local CSV files as an authoritative multi-user database.
---

# Human-reviewed handoff

A research stage is not finished simply because a file exists or the Agent says completed. Verify actual nonempty source/entity rows (where stage intended to have them), durable IDs, evidence pointers, status counts and pending review queue.

Run the local `research_pack.py validate` and `summary` against an explicit pack path before telling the next person to continue. Attach: objective/scope/revision, completed verified objects, HOLD/UNKNOWN cases, stop reason, observed provider spend if available, important source URLs, named reviewer and next recommended action.

**The primary final path is `marketops_handoff(run_id, operation_id)`.** It takes exactly one research snapshot —
the evidence-audited workbook — and then the experience draft plus the configured summary refresh. The legacy
`candidate_csv` field still comes back, but it now names **this workbook's `候选数据审计.csv`**; it is not a second
CSV research snapshot, and `marketops_export` no longer belongs to the main chain.

**Report the status honestly, not just the files.** `marketops_handoff` and `marketops_export_xlsx` both return
`experience_file` and `experience_refresh`. When the experience draft fails, or the summary refresh is not
`REFRESHED`, the status is `PARTIAL_HANDOFF_*` with `partial_reasons` — report that as PARTIAL/HOLD, **never** as a
clean handoff. The workbook that was really written is still named in `workbook_xlsx`; a failed experience side
never retracts it and never justifies claiming it does not exist. `SKIPPED_NOT_NEWER` means a newer snapshot already
exists on disk and was deliberately left alone. A reused delivery ID stays an error. Run `marketops_next` before
deciding a Run is finished: `READY_FOR_CANDIDATE_REVIEW` means structural prerequisites are present, not that
contacts are approved.

**Cross-session rules:** Files are an interchange format, not a concurrent DB. Only one writer should edit a given local pack at a time; create a new copy/revision for concurrent experiments, or use authorized MarketOps database API. Never silently write back to a customer's CRM or shared workspace just because the pack validates.

When the system is integrated later, only approved MarketOps MCP import can commit observations, facts and contact routes; preserve run/scene/evidence lineage and do not duplicate canonical objects.

No outreach is authorized by completion of a research handoff.