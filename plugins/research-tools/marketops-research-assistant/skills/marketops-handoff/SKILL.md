---
name: marketops-handoff
description: Check and transfer human-reviewed research-stage outputs between Codex, WorkBuddy and future MarketOps without treating local CSV files as an authoritative multi-user database.
---

# Human-reviewed handoff

A research stage is not finished simply because a file exists or the Agent says completed. Verify actual nonempty source/entity rows (where stage intended to have them), durable IDs, evidence pointers, status counts and pending review queue.

Run the local `research_pack.py validate` and `summary` against an explicit pack path before telling the next person to continue. Attach: objective/scope/revision, completed verified objects, HOLD/UNKNOWN cases, stop reason, observed provider spend if available, important source URLs, named reviewer and next recommended action.

**Report the summary refresh, not just the files.** `marketops_handoff` and `marketops_export_xlsx` return `experience_file` and `experience_refresh` alongside the CSV/XLSX. If `experience_refresh.status` is `FAILED`, say so with its `reason code` — the research deliverables are still real, but the user's one collectable Markdown did **not** move, and calling that a clean handoff would be false. `SKIPPED_NOT_NEWER` means a newer snapshot already exists on disk and was deliberately left alone.

**Cross-session rules:** Files are an interchange format, not a concurrent DB. Only one writer should edit a given local pack at a time; create a new copy/revision for concurrent experiments, or use authorized MarketOps database API. Never silently write back to a customer's CRM or shared workspace just because the pack validates.

When the system is integrated later, only approved MarketOps MCP import can commit observations, facts and contact routes; preserve run/scene/evidence lineage and do not duplicate canonical objects.

No outreach is authorized by completion of a research handoff.