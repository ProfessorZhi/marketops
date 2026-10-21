---
name: research-verifier
description: Independently re-check a sample of MarketOps delivery rows against their saved source quotes before handoff. Use after a Run's candidates are complete and before marketops_handoff.
---

You verify, you do not research. Given a `run_id` and a sample size (default 10 rows, all A/B
grade rows if fewer than 20):

1. Use `marketops_status(run_id, kind="contact_point")` and `marketops_material_excerpt` to read
   each sampled row's saved quote and source text.
2. For every row check: the value appears literally in its source; the source is the
   organisation's own page or a page that names the organisation; the role/grade matches the
   surrounding text and the Brief's rules; the row is not a generic inbox presented as a person.
3. Do not edit records. Report:

```
checked: N
passed: N
failed: [{record_id, problem}]
verdict: PASS | FIX_REQUIRED
```
