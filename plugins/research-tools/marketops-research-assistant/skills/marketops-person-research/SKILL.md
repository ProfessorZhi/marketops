---
name: marketops-person-research
description: Fill public professional role and literal contact gaps for existing target organizations; preserve person-role-contact joins and candidate-only status in the same Research Run.
---

# Person / role / contact research

**Trigger:** marketops_next recommends people, or an approved organization's sourced role/contact is missing. Do not restart source discovery for the whole market. **Input:** structured contact Brief, target organization keys, role priority and current sourced records; [contract](../../references/WORKFLOW_CONTRACT.md) defines joins.

Read current person/role/contact candidates with marketops_status filters. Search attributable official staff/team/contact pages or public professional documents under the existing scope/budget. Observation time is not proof that an old staff document still describes current employment.

Persist separate records with marketops_propose: `person` and `role` at stage people, `contact_point` at routes. Each carries organization_id and its own source material refs. A named person's role and contact use the **same person_id** matching the person's entity_key. A department-only route leaves person_id absent; do not attach a generic mailbox to a conveniently nearby person's name.

For contact_point use `type` and `value` from literal public source content. No guessed email patterns, no search-snippet emails, no fabricated evidence. Read the actual saved material and verify the literal value and association. A form/phone/profile is not automatically an email or a procurement decision-maker route. Unclear ownership is HOLD.

## Complete role and contact write

Persist a usable role/contact pair for one target before researching the next
target. Put the following fields inside `facts`, including `claims`; do not read
implementation files to discover this schema. Each quote is 12–600 literal
characters from the same saved artifact, whose ID is also in `artifact_ids`.

Synthetic example only: organization `org-example` already exists. Saved
`art-example` says “The Facilities team at Example Community Trust manages the
centre.” and “Contact the Facilities team at facilities@example.org.” Replace
all example values with actual same-Run evidence. This complete
`marketops_propose_batch` is a department route, so it has no person ID:

<!-- tested-example:department-role-contact -->
```json
{"run_id":"run-example","items":[
  {"record_id":"role-example-v1","stage":"people","kind":"role","entity_key":"role-example","facts":{"organization_id":"org-example","role_title":"Facilities team","role_function":"Manages the centre","claims":[{"field":"role","artifact_id":"art-example","quote":"The Facilities team at Example Community Trust manages the centre."}]},"artifact_ids":["art-example"]},
  {"record_id":"contact-example-v1","stage":"routes","kind":"contact_point","entity_key":"contact-example","facts":{"organization_id":"org-example","department":"Facilities team","type":"email","value":"facilities@example.org","claims":[{"field":"email","artifact_id":"art-example","quote":"Contact the Facilities team at facilities@example.org."}]},"artifact_ids":["art-example"]}
]}
```

For a genuinely named contact, first write a separate `person` with `name`,
`organization_id`, its identity quote and artifact IDs; add that person's
**same entity_key** as `person_id` to both role and contact. A department mailbox
does not satisfy a named-person Brief. If no valid route is found, write the
target's HOLD review and move on; do not keep rereading the same source.

A sourced role without a contact remains a route opportunity, not Grade B. A/B/C/D is a formal route assessment outside Lite authority; this tool rejects approval/reviewed_by/ready fields. Do not work around that rejection with legacy CSV-pack files. The structured named-person requirement cannot be satisfied by a department mailbox alone.

**Output:** read-back person/role/contact IDs, joins, evidence and unresolved facts. **Exit:** store a target disposition per contract after the bounded investigation. RESEARCHED_CANDIDATE cannot hide missing source/role/contact structure. Call marketops_next and hand off to evidence/acceptance. No outreach or form submission.

**Two representations:** derived Markdown can concatenate words and an email into a syntactically plausible but wrong address. Compare the literal value against separately saved `rendered_html`, captured `raw_response`, or URL-bound `provider_html`, and record its same-context quote. `provider_html` is vendor-cleaned HTML, not original HTTP or browser DOM. A provider JSON envelope or Markdown alone remains `HOLD_EMAIL_NOT_IN_PRIMARY_MATERIAL`. Literal presence still needs human/Scene review of role, recency and organizational binding. Use bounded `marketops_material_excerpt` queries instead of bringing entire contact pages into context.
