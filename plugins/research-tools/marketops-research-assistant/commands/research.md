---
description: Run a MarketOps research task end to end with minimal questions
argument-hint: <task, e.g. "Essex village halls hireable hourly, key contacts, xlsx">
---

Use the `marketops-research` skill to complete this research task autonomously:

$ARGUMENTS

Ask at most once, up front, only for what is missing among: scope (area, entity type, roles),
delivery format and the budget cap for this task. Then run the whole flow without asking again:
build the source and venue/organisation pools, triage, dispatch organisations to the
`account-researcher` subagent in batches of 5–10 (or work through them yourself if subagents
are unavailable), have `research-verifier` check a sample, and deliver with `marketops_handoff`.

Work through the `marketops_*` tools: no hand-written workbook, no direct reads of the plugin's
databases or data directory. Directory and annuaire pages: `marketops_directory_extract`. Open-data
exports: `marketops_pool_import`. If a tool genuinely cannot do what the page needs, you may read the
saved artifact with your own code — the facts you stage must still quote that artifact, and provider
calls still go through `marketops_*`; record the blocker with `marketops_note`. When a tool refuses,
follow its message (for quotes, copy the closest page text it shows); after two failed tries on a
target, record HOLD and continue. Always end with `marketops_handoff`, even for a partial result.

Stop early only if the budget cap is about to be reached, a paid tier needs authorisation,
the same rule conflict keeps recurring, or a credential is missing. Report at the end:
organisations covered, admitted routes by grade, HOLDs with reasons, provider calls and spend.
