# Unified Lite Research workflow contract

Version: 2026-10-16. This contract governs the **Research MCP path**, not the optional legacy portable CSV-pack commands. Skills teach methods; the store holds candidate state; deterministic checks recommend work; source review and authorized SOP review remain separate.

## Progressive loading and reference resolution

Metadata exposes 17 Skills. Start with `marketops-research`; load only the selected stage Skill. Load this contract once when configuring a Run or resolving a schema question. Load provider details only after a concrete capability is needed. Do not preload all business SOPs or all provider manuals.

Paths in Skills are relative to the containing SKILL.md. `../../references/` is the shared reference directory **inside the complete plugin bundle**. Install/copy the complete plugin, not a naked Skill directory. Packaging must retain every linked document within that bundle; absolute development paths and links to a parent repository are not portable dependencies. A standalone Skill-only client may teach methods but must not claim Research MCP runtime success.

## First use and a single Run

Call `marketops_setup_status` before stateful operations. Only CONFIG_REQUIRED asks the user for (1) an absolute data directory and (2) an absolute experience Markdown file path. Do not invent either path. Existing configuration is not a reason to ask again. Explicit environment overrides remain advanced overrides, not required WorkBuddy onboarding fields. The installer chooses code/toolchain locations only; it must not preselect or create an unchosen install/data directory.

Call `marketops_start(run_id, scope, objective, rule_version)` with the user's actual scope. An unknown SOP version stays UNKNOWN; never manufacture approved business rules. Persist a structured brief with `marketops_propose`, stage `qualification`, kind `observation`, stable record/entity IDs and facts:

```json
{"record_type":"research_brief","target_kind":"organization","goal":"contacts","minimum_targets":null,"require_named_person":true,"evidence_requirements":{"organization":["identity"]}}
```

`target_kind` is `venue` or `organization`; `goal` is `pool` or `contacts`. Contact goals target organizations. `minimum_targets`, when supplied, is an explicit requested candidate count, not a claim about total market size. **If the user did not request a quantity, omit it or store null**: the result reports quantity_target_status=UNSPECIFIED. Do not invent a quota or ask an unnecessary question just to satisfy the planner. A completed current candidate batch does not establish market coverage.

`require_named_person` must be a boolean; true is only valid for contacts. For a venue pool use `venue` / `pool` / false. Extract these values from a clear user request; clarify only genuinely missing business choices. Do not silently default a department-contact task to named procurement contacts or a venue-only task to sales research. Older Runs without this record return BRIEF_REQUIRED, not false completion.

## Candidate records and joins

All stage outputs use `marketops_propose` and the same SQLite Run, **not hand-edited CSV tables**. CSV is an export. Reuse one stable opaque `entity_key` for the same known entity; record IDs identify append-only observations, not entities. Distinguish a venue, its owner, its operator and a purchasing organization. A website/domain is not a unique venue identity.

| kind | stage | minimum useful facts / joins |
|---|---|---|
| source | discovery | url, title, provider, operation_id; discovery_only=true |
| venue | entities | name; organization_id only when supported |
| organization | entities | name; official-domain claim only when supported |
| relationship | entities | venue_id, organization_id, relationship_type |
| person | people | name, organization_id |
| role | people | organization_id, role_title or role_function; person_id for named claims |
| contact_point | routes | organization_id, type, value; person_id for a named contact |
| contact_route | routes | source-backed proposed relationship, never a formal grade |
| review | review | reason and the unresolved IDs; schema below for target disposition |

`artifact_ids` are real saved material IDs belonging to this Run. Never invent IDs or copy an earlier Run's IDs into a new Run. Search snippets, model decisions and challenge pages are not evidence of identity, employment, purchasing authority or an email. A named-contact target needs the **same person entity** linked by its sourced role and sourced contact point; an unrelated staff name beside a generic department mailbox does not qualify.

Use `marketops_status` with `limit`, `cursor`, optional `kind` and `entity_key`. Iterate `next_cursor` until empty when a full audit is needed; use filters for ordinary stage work. A page set fixes a row high-water mark; later append-only records appear in a new query, not midway through the old snapshot. A cursor belongs to its Run and filters. `marketops_next(run_id, limit=25)` gives bounded current tasks without dumping every candidate into context.

Cross-Run canonical merging is **not implemented**. Reusing a known key does not automatically merge facts or prove freshness. Keep potential duplicates visible; re-acquire/revalidate evidence for a new Run. Never advertise automatic cross-Run deduplication.

## Append-only corrections and target dispositions

To correct a record, append a new record of the same kind/entity with `supersedes` naming the previous record_id. Never overwrite history. Workflow checks accept only an unbroken, single-tip chain; a fork, cycle or foreign predecessor is a conflict. Superseded person/role/contact records no longer contribute to structural completion. A brief correction must use the same brief entity key and reflect the user's changed requirement, not an Agent lowering its goal to pass a check.

After research/review, append kind `review`, stage `review` with facts:

```json
{"review_type":"target_disposition","target_kind":"organization","target_entity_key":"org-001","disposition":"HOLD","reason":"Public role source missing; approved methods exhausted."}
```

Closed dispositions: RESEARCHED_CANDIDATE, HOLD, EXCLUDED, DUPLICATE. Each needs a short factual reason. Corrections use `supersedes` in the same target's disposition chain. RESEARCHED_CANDIDATE is **not approval** and cannot suppress missing entity/role/contact material.

HOLD is an unresolved target and remains a visible goal gap. A justified EXCLUDED or DUPLICATE is a resolved disposition, **not itself a failure**, and remains visible in disposition_counts; it does not count toward an explicit positive-candidate minimum. An all-excluded sample is a legitimate negative research result, not a successful positive-candidate delivery or proof that the market has no customers. Do not keep repeating the same blocked method or expand budget to fill a quota.

## Exit and delivery

After a bounded batch, call `marketops_next` again. It recommends a stage, not additional spending authority. READY_FOR_CANDIDATE_REVIEW means structural prerequisites are present; perform source meaning/age/ownership checks and independently audited export. It is never READY_CONTACT, a formal A/B/C/D grade, verified procurement authority, or complete market coverage.

The primary final path is `marketops_handoff(run_id, operation_id)`: one evidence-audited XLSX/CSV/manifest snapshot, an experience draft, and the configured experience-summary receipt. `marketops_export_xlsx` is the explicit workbook-only path. Do not call both with the same delivery ID or regenerate a second research snapshot just to make another format. `marketops_export` is a legacy candidate CSV utility; it is not the main workflow contract. Immutable path conflicts must remain errors.

At delivery, structural goal gaps from the **same record snapshot as the workbook** are attached to its manifest and recorded in the existing journal. A successful file export and an unmet business goal can coexist; report both. Missing structured goals are UNKNOWN, not inferred from prose. A failed experience refresh is not a clean delivery. Summary and draft failures after an existing workbook are PARTIAL/HOLD, never a claimed rollback of that workbook.

## Experience and trust boundary

The user collects one configured Research_Experience.md across Runs. Automatic entries state observations/counts, not causes. Agent notes separately state problem, action, result, optional explicitly uncertain hypothesis and opaque evidence references. No hidden reasoning, raw contact lists, URLs, credentials or cookies in the collectable note. Log retries, corrections, holds and the gap between request and result. Accumulate candidates for later human review; do not automatically edit Skills, SOPs, code, permissions or formal contact grades.

## Evidence, efficient context and quantity unit (generic supplement)

**No fixed market assumptions.** The plugin handles named entity kinds and arbitrary brief-defined evidence keys; country, sector, product, surface, licensing, buyer role, exclusion policy and search localization all belong to the selected user task or a separately approved Scene/Profile. Do not bake a particular UK venue SOP into shared Python or the universal Research Skill.

When a genuinely mandatory `minimum_targets` is set, also set `minimum_targets_unit` equal to `target_kind` (venue or organization). Aspirational counts such as “try 10–15 sites” are not hard organization-contact quotas. Wrong/missing unit is BRIEF_INVALID; correct append-only via the same brief key + `supersedes` rather than changing the historical record.

Record-specific support uses `facts.claims=[{"field":"identity","artifact_id":"...","quote":"actual published fragment"},...]`. `artifact_ids` must contain the same saved artifact. At minimum: `identity` for venues and organizations, `relationship` for typed relations, `exclusion` for hard excluded target dispositions. A Scene may add other required keys through `facts.evidence_requirements={"venue":["location","surface"],...}` **in the brief**, not code. Literal quote matches are auditable links, never proof of semantic truth, present employment, suitability or approval. Missing/derived-only material is visible as HOLD and gap codes in `marketops_evidence_audit` and the workbook manifest.

A normal budget-conscious batch: call next(limit=10), use filtered status(limit=20), read <=1200 characters via material_excerpt, submit up to20 records atomically with propose_batch, then audit a compact summary. Do not load 17 Skills or all pages. One Run; resume after restart. Local `rendered_html` is a post-render DOM representation, not byte-for-byte HTTP raw; `provider_extracted` is derivative Markdown. The original bytes are UNKNOWN unless independently captured. Use an immutable delivery snapshot and do not attempt to rewrite an older workbook to hide corrected evidence.
