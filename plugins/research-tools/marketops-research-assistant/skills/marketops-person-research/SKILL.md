---
name: marketops-person-research
description: Fill public professional role and literal contact gaps for existing target organizations; preserve person-role-contact joins and candidate-only status in the same Research Run.
---

# Person / role / contact research

**Trigger:** marketops_next recommends people, or an approved organization's sourced role/contact is missing. Do not restart source discovery for the whole market. **Input:** structured contact Brief, target organization keys, role priority and current sourced records; [contract](../../references/WORKFLOW_CONTRACT.md) defines joins.

Read current person/role/contact candidates with marketops_status filters. Search attributable official staff/team/contact pages or public professional documents under the existing scope/budget. Observation time is not proof that an old staff document still describes current employment.

Persist separate records with marketops_propose: `person` and `role` at stage people, `contact_point` at routes. Each carries organization_id and its own source material refs. A named person's role and contact use the **same person_id** matching the person's entity_key. A department-only route leaves person_id absent; do not attach a generic mailbox to a conveniently nearby person's name.

For contact_point use `type` and `value` from literal public source content. No guessed email patterns, no search-snippet emails, no fabricated evidence. Read the actual saved material and verify the literal value and association. A form/phone/profile is not automatically an email or a procurement decision-maker route. Unclear ownership is HOLD.

A sourced role without a contact remains a route opportunity, not Grade B. A/B/C/D is a formal route assessment outside Lite authority; this tool rejects approval/reviewed_by/ready fields. Do not work around that rejection with legacy CSV-pack files. The structured named-person requirement cannot be satisfied by a department mailbox alone.

**Output:** read-back person/role/contact IDs, joins, evidence and unresolved facts. **Exit:** store a target disposition per contract after the bounded investigation. RESEARCHED_CANDIDATE cannot hide missing source/role/contact structure. Call marketops_next and hand off to evidence/acceptance. No outreach or form submission.

**Two representations:** derived Markdown can concatenate words and an email into a syntactically plausible but wrong address. For an email candidate, compare the literal value to the separately saved rendered HTML (`material_kind=rendered_html`) and record a short same-context `claims` excerpt. If HTML is absent, report `HOLD_EMAIL_NOT_IN_RENDERED_HTML` rather than guess or promote. A verified literal in rendered DOM still needs human/Scene review of role, recency and organizational binding. Use bounded `marketops_material_excerpt` queries instead of bringing entire contact pages into context.
