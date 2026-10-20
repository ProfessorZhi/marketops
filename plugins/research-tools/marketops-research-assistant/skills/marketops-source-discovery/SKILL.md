---
name: marketops-source-discovery
description: Discover bounded public sources only when the current Run lacks target sources; store traceable source candidates through Research MCP without promoting snippets to facts.
---

# Source discovery

After the first one or two targets have usable saved material, persist their
organization/venue candidates with the venue-account Skill's complete batch
example before searching for more targets. Source rows alone do not satisfy the
business task. When a target is unresolved within the approved effort, persist
its target record and HOLD review so the final handoff can include that gap.

**Trigger:** marketops_next recommends discovery, or an approved target has a concrete source gap. **Input:** persisted Brief, current Run and existing source candidates. Read [workflow contract](../../references/WORKFLOW_CONTRACT.md) only when needed.

Read `marketops_status(run_id, kind="source")` first and reuse appropriate saved material. Fix country/language, the user's scope and a small query budget. Load `marketops-provider-router` only if an actual external capability is needed. SerpApi/Serper via `marketops_search` and configured TinyFish search are discovery options; do not assume free use or silently fail over to a billable provider.

`marketops_search` returns stored_record_ids: read those back rather than creating duplicate source rows. For other actual receipts, use `marketops_propose(stage="discovery", kind="source")` with url/title/provider/operation_id and discovery_only=true. **Do not create or edit sources.csv as the inter-Skill data store.**

A directory page can describe many venues. Preserve page identity separately from candidate entity identity; neither one-domain-one-venue nor one-search-hit-one-qualified-company is valid. Snippets/Maps/Place IDs do not establish legal identity, ownership, current employment, purchasing responsibility or an email.

Zero hits require a bounded attempt record and a stop reason, not a claim that a market contains no customers. Fetch selected public pages through the permitted evidence path; challenge/login pages stay blocked. No bypass of access controls.

**Output:** actual stored source IDs, material references if acquired, unresolved gaps and attempts. **Exit:** read back saved source records, then call marketops_next; hand off the indicated batch to venue-account/evidence, not a prose-only or CSV-only result. Record substantive search failure or goal shortfall via marketops_note without raw URLs or contacts in the collectable note.
