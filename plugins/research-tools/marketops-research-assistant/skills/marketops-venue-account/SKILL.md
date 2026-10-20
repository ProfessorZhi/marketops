---
name: marketops-venue-account
description: Extract distinct venue, organization and responsibility candidates from saved Run material; preserve evidence and entity identity through the unified SQLite contract.
---

# Venue / Account candidates

**Trigger:** saved sources/material exist but requested target entities are missing, or a target has an identity/relation gap. **Input:** the same Run's Brief, source/material IDs and current venue/organization records. Contract: [workflow](../../references/WORKFLOW_CONTRACT.md).

Keep two entry paths: asset-driven venue discovery and account-driven organization discovery. A distributor/company task need not invent a venue. A directory or council website may list many distinct venues. Owner, operator, maintainer and procurer may be different organizations; a domain or public telephone does not prove the relationship.

Read existing targets with marketops_status filters. Use marketops_propose for separate `venue`, `organization` and `relationship` records at stage `entities`, with stable entity keys, explicit joins and actual in-Run artifact_ids. Known organization joins use `organization_id`; relationship records additionally use venue_id and relationship_type. Never hand off by editing venue_candidates.csv, venues.csv or organizations.csv.

Every assertion remains a candidate. Per-Scene eligibility is a source-backed assessment proposal, not an intrinsic organization property or a contact grade. Unknown facts stay missing/HOLD. Jev may help interpret already-saved content but its answer is not independent evidence or approved SOP.

## Write the current target now

After one or two targets, persist them before fetching more pages. No source-code
read is needed. `facts.claims` contains at most 12 `{field, artifact_id, quote}`
objects; each literal quote is 12–600 characters and its artifact ID must also
be in that item's `artifact_ids`. Find saved IDs with
`marketops_status(run_id, kind="artifact")`; read at most 3 artifacts / 3 queries
with `marketops_material_excerpt`.

Synthetic example: `art-example` in `run-example` says
“Example Community Trust operates Example Centre.” Replace these IDs and facts
with your actual Run material. This complete batch separates the venue, the
organization and the supported operator relation; it asserts no surface type or
purchasing authority.

<!-- tested-example:venue-organization-relation -->
```json
{"run_id":"run-example","items":[
  {"record_id":"org-example-v1","stage":"entities","kind":"organization","entity_key":"org-example","facts":{"name":"Example Community Trust","claims":[{"field":"identity","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]},
  {"record_id":"venue-example-v1","stage":"entities","kind":"venue","entity_key":"venue-example","facts":{"name":"Example Centre","organization_id":"org-example","claims":[{"field":"identity","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]},
  {"record_id":"relation-example-v1","stage":"entities","kind":"relationship","entity_key":"relation-example","facts":{"venue_id":"venue-example","organization_id":"org-example","relationship_type":"operates","claims":[{"field":"relationship","artifact_id":"art-example","quote":"Example Community Trust operates Example Centre."}]},"artifact_ids":["art-example"]}
]}
```

Use the target's stable `entity_key` in every join. Missing identity or required
facts remain UNKNOWN/HOLD; do not manufacture a claim to fill a schema. The
[contract's HOLD batch](../../references/WORKFLOW_CONTRACT.md#write-one-target-before-researching-the-next-batch)
shows how to persist an unverified requested target.

Detect conflicting observations/duplicate keys before adding more. Append explicit `supersedes` corrections rather than overwrite history. No automatic cross-Run canonical merge exists; similar names/domains are not proof of identity. Do not reuse foreign Run artifact IDs.

**Output:** read-back candidate IDs/joins/material refs and unresolved conflicts. **Exit:** call marketops_next. Venue-pool tasks may proceed to review without people research; contact tasks proceed only for their actual organization targets. Bounded exhausted research gets a target disposition with reason, not invented completion.

**Evidence gate:** for each physical object attach an identity excerpt containing the actual object, plus any **Scene-defined** facts (surface/location/licence/category/etc.) required by its brief. A directory address is not proof of surface or purchasing control. For typed venue→organization edges attach `claims` containing a relationship quote from the same source; otherwise leave it candidate/HOLD, not operator/owner/procurer as a confirmed fact. For 2–20 independent candidates use one atomic `marketops_propose_batch` (never inside an unrelated real Run for tests), then a small `marketops_evidence_audit` response.
