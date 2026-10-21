---
name: marketops-jev
description: Jev typed judgments through MarketOps - the speed layer that classifies and ranks saved pages so you do not read each one (venue type, page value, role group, grade pre-check, same-entity). Load before triaging more than a handful of targets.
---

# Jev: let it read, you decide

A Jev judgment costs a few hundred tokens and none of your context; reading a page yourself costs
thousands. Use Jev wherever the same small question repeats.

**Many pages at once**: `marketops_jev_triage(run_id, operation_id, artifact_ids=[...],
question, options, threshold=0.85)` returns one row per page with choice and confidence; rows under
the threshold are `needs_review` - judge only those from the source.
**One page**: `marketops_jev_choice(run_id, operation_id, source_excerpt=<artifact_id>, question,
options, primitive)`.

**Inputs**: `source_excerpt`/`artifact_ids` are saved artifacts of this Run. `options` are snake_case
labels; always include `unknown`.

| Use | Question | Options |
|---|---|---|
| Scope triage | Is this target in the brief's scope? (e.g. natural-grass pitch, coworking space) | `in_scope`, `out_of_scope_<type>`, `unknown` |
| Page value | Does this page list staff or contact details? | `contacts`, `no_contacts`, `unknown` |
| Role group | Which role does the contact in this text hold? | the scene pack's role groups + `unknown` |
| Grade pre-check | Which grade fits this route? | `a`, `b`, `c`, `d`, `x`, `unknown` |
| Hire / fit | Does this page offer hourly hire? | `hourly_hire`, `events_only`, `unknown` |

`primitive='noul'` for a yes/no probability (near 0.5 = unsure), `'score'` for ordered levels.

**Rules**: Jev ranks and filters; it never establishes a fact, a final grade or a merge. Facts come
only from quotes. At ≥0.85 confidence its answer is a candidate; below, you check the source.

**Efficient use**: about $0.0001 per judgment (input tokens only), so use it freely at volume, but
only on targets still in scope: drop excluded and duplicate targets first, ask one question per
page, never ask again about a page already judged. `harvest_batch(roles_wanted=...)` tags every
email with no extra call from you.

**When Jev is unavailable** (no key, out of credit, refused): tags come back `ungraded:...`. Judge
from the short contact context in the harvest result rather than whole pages, keep a smaller
shortlist, and say in the report that judgment cost moved to you.
