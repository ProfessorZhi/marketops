# Unified Lite Research workflow contract

Version: 2026-10-20. This contract governs the **Research MCP path**, not the optional legacy portable CSV-pack commands. Skills teach methods; the store holds candidate state; deterministic checks recommend work; source review and authorized SOP review remain separate.

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

### Write one target before researching the next batch

Use `marketops_status(run_id, kind="artifact")` to find this Run's saved material
IDs, then `marketops_material_excerpt` with at most 3 artifact IDs and 3 queries.
After one or two targets have usable quotes or a clear unresolved gap, call
`marketops_propose_batch` immediately. A retrieved page alone is not an
organization, role or contact candidate. Do not defer all candidate writes until
every target has been researched, and do not read implementation files to learn
the write format: the complete format is below.

`items` contains 1–20 objects. Each item has exactly these keys:
`record_id`, `stage`, `kind`, `entity_key`, `facts`, `artifact_ids`.
The business fields and `claims` go **inside `facts`**. Each claim has
`field`, `artifact_id`, `quote`; use at most 12 claims, each quote 12–600
characters, copied from the same Run's saved material. The claim's `artifact_id`
must also occur in that item's `artifact_ids`. Do not supply `approved`, `ready`,
`verified`, `send_authorized` or `reviewed_by`.

**Synthetic example only.** Suppose the saved `rendered_html` artifact
`art-example` in `run-example` contains this page. In a real Run, replace all
example IDs, facts and quotations with values actually read from that Run;
never write this example as research evidence.

<!-- tested-source:example-material -->
```html
<main><p>Example Community Trust operates Example Centre.</p><p>The Facilities team at Example Community Trust manages the centre.</p><p>Contact the Facilities team at facilities@example.org.</p></main>
```

For an organization-contact Brief with `require_named_person=false`, this is a
complete `marketops_propose_batch` call. Joins use the organization's
**entity_key**, not its record ID. A department route has no `person_id`.

<!-- tested-example:department-route -->
```json
{
  "run_id": "run-example",
  "items": [
    {"record_id":"org-example-v1","stage":"entities","kind":"organization","entity_key":"org-example","facts":{"name":"Example Community Trust","claims":[{"field":"identity","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]},
    {"record_id":"role-example-v1","stage":"people","kind":"role","entity_key":"role-example","facts":{"organization_id":"org-example","role_title":"Facilities team","role_function":"Manages the centre","claims":[{"field":"role","artifact_id":"art-example","quote":"The Facilities team at Example Community Trust manages the centre."}]},"artifact_ids":["art-example"]},
    {"record_id":"contact-example-v1","stage":"routes","kind":"contact_point","entity_key":"contact-example","facts":{"organization_id":"org-example","department":"Facilities team","type":"email","value":"facilities@example.org","claims":[{"field":"email","artifact_id":"art-example","quote":"Contact the Facilities team at facilities@example.org."}]},"artifact_ids":["art-example"]},
    {"record_id":"review-example-v1","stage":"review","kind":"review","entity_key":"review-org-example","facts":{"review_type":"target_disposition","target_kind":"organization","target_entity_key":"org-example","disposition":"RESEARCHED_CANDIDATE","reason":"Saved source identifies the organization and a literal department contact; business fit and human approval remain pending."},"artifact_ids":["art-example"]}
  ]
}
```

Then call `marketops_evidence_audit` and `marketops_next` once. Quote matches
support a candidate; they do not certify buying authority or satisfy missing
Brief-specific evidence. If the Brief requires a named person, also create a
`person` at `people` with `name`, `organization_id` and its own identity quote;
both role and contact must use that person's **same entity_key** as `person_id`.
Do not invent a person for the department-only example.

If the requested target has no source-backed identity or contact, retain that
gap instead of inventing `claims`. This valid batch preserves an explicitly
unverified user-supplied target plus a HOLD. The audit will correctly report
missing evidence; handoff can still deliver the partial result.

<!-- tested-example:unknown-target-hold -->
```json
{
  "run_id":"run-example",
  "items":[
    {"record_id":"org-unresolved-v1","stage":"entities","kind":"organization","entity_key":"org-unresolved","facts":{"name":"Unresolved Example Trust","identity_status":"UNKNOWN","input_basis":"User-supplied research target; official identity not established"},"artifact_ids":[]},
    {"record_id":"review-unresolved-v1","stage":"review","kind":"review","entity_key":"review-org-unresolved","facts":{"review_type":"target_disposition","target_kind":"organization","target_entity_key":"org-unresolved","disposition":"HOLD","reason":"Approved methods did not establish official identity or a role-bound public contact."},"artifact_ids":[]}
  ]
}
```

HOLD does not need a fabricated quote. A hard `EXCLUDED` disposition does need
an actual source and `field="exclusion"` claim. Use a new record ID and
`facts.supersedes` for a correction; keep the same entity key and prior record.

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

When a genuinely mandatory `minimum_targets` is set, also set `minimum_targets_unit` equal to `target_kind` (venue or organization). Aspirational counts such as “try 10–15 sites” are not hard organization-contact quotas. An explicit mismatched unit is BRIEF_INVALID; a legacy brief without a unit is BRIEF_UNIT_REVIEW_REQUIRED (never silently apply its quota). Correct append-only via the same brief key + `supersedes` rather than changing the historical record.

Record-specific support uses `facts.claims=[{"field":"identity","artifact_id":"...","quote":"actual published fragment"},...]`. `artifact_ids` must contain the same saved artifact. At minimum: `identity` for venues and organizations, `relationship` for typed relations, `exclusion` for hard excluded target dispositions. A Scene may add other required keys through `facts.evidence_requirements={"venue":["location","surface"],...}` **in the brief**, not code. Literal quote matches are auditable links, never proof of semantic truth, present employment, suitability or approval. Missing/derived-only material is visible as HOLD and gap codes in `marketops_evidence_audit` and the workbook manifest.

A normal budget-conscious batch: call next(limit=10), use filtered status(limit=20), read <=1200 characters via material_excerpt, submit up to20 records atomically with propose_batch, then audit a compact summary. Do not load 17 Skills or all pages. One Run; resume after restart. Local `rendered_html` is a post-render DOM representation, not byte-for-byte HTTP raw; `provider_extracted` is derivative Markdown or the provider's response envelope. TinyFish `provider_html` is URL-bound, vendor-cleaned semantic HTML stored separately from that envelope: literal quotations are auditable, but original HTTP, browser DOM and business meaning are not verified. The original bytes are UNKNOWN unless independently captured. Use an immutable delivery snapshot and do not attempt to rewrite an older workbook to hide corrected evidence.

## Anti-loop and access-wall contract (scene-neutral)

Persisted Run state—not the Agent's conversation—is the checkpoint. Each bounded batch works on `marketops_next` target IDs with one selected provider path. Retain `snapshot_id`; after an actual attempted action call `marketops_next(previous_snapshot_id=<token>, after_attempt=true)`. An unchanged snapshot means **no new persisted record**, not that the market has no customers. Do not keep cycling through the same source, search or Skill. Switch to another independently authorized target/public source, record HOLD with an observable reason, or stop. Normal read-only status checks do not signal a stall.

Existing paid-search and provider attempt ledgers prevent replay of an identical fingerprint even if the Agent changes `operation_id`. A known public-host 403/429 or challenge in the current Run blocks later crawl/fetch attempts on that host. Changing the URL path, provider, identity or browser fingerprint does not authorize bypassing Cloudflare/CAPTCHA. Use a separate legitimately accessible official/public source, reuse saved material or HOLD; never assume an unknown/timeout request was unbilled.

`marketops_events` returns a max-20 recent event view and bounded full-Run blocker counts. `marketops_note` captures short observed symptom/action/result/uncertain hypothesis once per mechanism and refreshes the configured experience file. No private reasoning traces, raw web documents or arbitrary execution transcripts go into this file. Continue the same Run instead of restarting discovery, and stop at the approved call/time budget.

## Provider coverage, first-call Brief and cost gates

A **new** Run should call `marketops_start(run_id,scope,objective,rule_version,brief={...})` once, so the structured goal is durably saved **before** a managed network attempt. An old Run without a Brief stays `BRIEF_REQUIRED` and the new managed Search/Crawl/Provider path refuses a request; append the missing same-Run structured Brief via `marketops_propose` after user policy review. Never guess the goal from prose or invent an explicit quantity.

Use `marketops_capabilities(task_kind=<supported task>)` to view low-context route suggestions; they are advisory only. Official external MCPs are not auto-installed and host-reported names are not auth, wallet or rate proof. The five-vendor [onboarding](PROVIDER_ONBOARDING.md) distinguishes MarketOps data storage, each vendor's key/OAuth, TinyFish target-site Browser Context Profile, host spending authorization, per-Run allowed domains, provider-wallet/credit verification and research-only actions.

Do not paste API keys into chat or Git, don't treat a logged-in Google identity as universal site access, don't use Cloud/Agent to bypass CAPTCHA, and don't let provider error 402/429/401 silently pick a second paid vendor.
