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
- Create `intake.json` in a user-authorized research pack; changes to scope require a new revision and review, never silently edit history.

Hand off to `marketops-source-discovery`. Output first: 2-4 sentence interpretation; secondly a small set of unknowns and the next *authorized* action.