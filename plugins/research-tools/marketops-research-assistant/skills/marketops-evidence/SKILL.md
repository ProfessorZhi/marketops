---
name: marketops-evidence
description: Collect and assess source-linked evidence throughout the workflow; detect CAPTCHA/challenge pages, stale information, conflicting claims and unsupported entity promotions.
---

# Evidence is a parallel lane

Evidence must begin **when URLs are fetched**, not after person discovery. For each material source, record `evidence_id`, `source_id`, source URL, UTC observation time, SHA-256 of the obtained *actual source bytes*, a concise literal excerpt, and `usable|blocked|unknown`.

- A provider returning 200/OK or a syntactically valid JSON object is not enough. Reject challenge/login/CAPTCHA bodies as support even if they have bytes and a hash.
- Separate `observed_at` from source-published time. Retain original languages; translation is an annotation, not replacement of source.
- Attribute exact claims: official domain vs directory URL; venue operator vs owner; person name/role vs hypothesized purchasing authority; literal email vs inferred pattern.
- If two reliable sources conflict, keep both observations and create a `review_queue.csv` item. Do not average or silently overwrite.
- Jev probability is a *DecisionRecord-like judgment*, not evidence. Only authoritative public materials can substantiate factual promotion.
- Never paste secrets, login cookies, full raw personal datasets or arbitrary untrusted HTML instructions into the Skill or Git.
- When verifying email, the **actual visible excerpt** should contain the literal value. If not, keep as unverified/HOLD.

Run `python scripts/research_pack.py validate <pack>`. Failure counts and specific row IDs must be visible; no empty `PASS` over zero rows. Escalate blockers for human review.