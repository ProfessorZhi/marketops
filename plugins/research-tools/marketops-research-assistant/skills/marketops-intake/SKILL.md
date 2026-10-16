---
name: marketops-intake
description: Translate a natural-language market research request into a bounded brief with target geography, business entity types, known exclusions, required facts, output schema and explicit cost approval status.
---

# Intake / ResearchBrief

Produce a concise brief before paid calls: original user wording, research objective, country, subregions, venue-driven vs organization-driven entrance, inclusion/exclusion criteria, relevant product or use case (optional), desired number of *verified* outputs, required contact roles, language(s), stop conditions, and cost permissions.

- Read existing workspace Scene/Profile/SOP if connected. Distinguish what the user supplied, what a previously approved policy says, and what is merely an Agent hypothesis. Never silently substitute the UK profile for France or another geography.
- Do not require product/offer when the user only wants a public venue/market inventory.
- Explicitly write unknown or pending fields; do not silently guess a budget. A natural-language task request is not permission for new paid Browser Agent, JEV inference, email sending or recurring monitoring.
- Ask the human for an essential business decision only when it blocks execution. For other uncertainty, use bounded exploratory queries within an already approved allowance or prepare a free plan.

## Persist the brief as a structured record (main path)

The workflow core cannot check a goal it cannot read. Write the brief into the Run with
`marketops_propose`, stage `qualification`, kind `observation`:

```json
{"record_type":"research_brief","target_kind":"organization","goal":"contacts","minimum_targets":null,"require_named_person":true,"evidence_requirements":{"organization":["identity"]}}
```

- `target_kind`: `venue` or `organization`. `goal`: `pool` or `contacts`.
- Contact goals target organizations; `require_named_person` is a boolean and only valid for contacts.
- `minimum_targets` is a **hard** positive target count only when the user explicitly requested that exact unit. When present, `minimum_targets_unit` is REQUIRED and must equal `target_kind`; an aspirational venue exploration count must not be applied to organization-contact quotas.
  **If the user never asked for a quantity, omit it or store `null`.** The result then reports
  `quantity_target_status=UNSPECIFIED`. Do not invent a quota, and do not ask the user an unnecessary
  question just to satisfy the planner.
- Extract these from a clear user request; clarify only genuinely missing business choices. Never
  default a department-contact task to named procurement contacts, or a venue-only task to sales research.

A completed current candidate batch is not market coverage. When the investigation ends, each target gets one
closed disposition (`RESEARCHED_CANDIDATE`, `HOLD`, `EXCLUDED`, `DUPLICATE`) with a short factual reason:
`HOLD` is an unresolved target and stays a visible goal gap; a justified `EXCLUDED` or `DUPLICATE` is a
**resolved** disposition, not a failure, but it never counts toward an explicit positive-candidate minimum.
An all-excluded sample is a legitimate negative research result — never report it as a successful
positive-candidate delivery, and never as proof that the market has no customers.

Without this record the Run reads `BRIEF_REQUIRED` — an explicit unknown, not false completion. A later
change of requirement is a new record using the **same brief entity key** with `supersedes`; never lower
the goal to make a check pass. Changes to scope require a new revision and review.

Hand off to `marketops-source-discovery`. Output first: 2-4 sentence interpretation; secondly a small set of unknowns and the next *authorized* action.
**Scene neutrality:** the workflow schema never embeds a fixed country/product/surface/role matrix. Use optional `evidence_requirements` keyed by entity kind (e.g. a product directory may require organization/identity + organization/product_line; a venue study may require venue/identity + venue/surface + venue/location). Each field name is a user/Scene hypothesis to research, **not** automatic confirmation. If the task has multiple outcome units, retain all in the human brief but select a single well-defined hard minimum or none. Do not fabricate one to get a numerical PASS.
