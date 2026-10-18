# Preparing outbound work and checking what a claim is allowed to say

Use when the request is about **producing something for an organisation to
read**: a message, a handoff, or the question "are we allowed to say this".

## A claim is a fact with a source, not a sentence someone liked

Every assertion in outbound work has to trace to something the server holds.
Before writing, and again before presenting a draft as ready:

1. Read the facts the text will rest on — `mcp:get_account` for the
   organisation, `mcp:get_evidence` for the artefact a claim came from. A claim
   whose source cannot be opened is not a claim; drop it rather than soften it.
2. Read what the draft is allowed to say under the current writing rules. If a
   draft cites a claim that has since been withdrawn, the affected **unsent**
   drafts go on hold — that outcome is reported by the server
   (`mcp:withdraw_claim`), and it is not something to re-derive locally.
3. Read existing outbound work with `mcp:list_handoffs` before creating more. A
   second message to the same organisation is a fact the user needs to be told
   about, not a detail to hide.

## Producing a draft

`mcp:draft_outreach` produces the draft **and the record of what it rests on**.
The two travel together: present the draft with its evidence references, so the
user can see why each sentence is there. A draft presented without them is not
reviewable.

Do not write a draft from the conversation alone. If the facts needed are not on
the server yet, the correct output is to say which fact is missing — not to
write around it.

## "Is this allowed?"

Two different questions wear the same words. Separate them:

* **A fact question** — is this true, and is the evidence still current? Answer
  from `mcp:get_evidence` and the freshness the server reports.
* **A rule question** — is this kind of statement permitted for this scenario?
  That is policy, and policy is not editable from a chat. Read the rules in
  force and the candidate changes with `mcp:list_policy_patches`; propose a
  change with `mcp:propose_policy_patch`. Approval is a separate use case with
  its own role and is **not reachable from this package**.

A rule suggestion that came from one organisation's experience is a candidate
for the whole scenario, and the two must not be conflated: the impact preview
(`mcp:preview_feedback_impact`) shows the scope before anything is proposed as a
rule. A single sentence never edits policy.

## Handoff is a transfer of responsibility

A handoff moves work to a person; it does not notify them. Read the handoff's
own state rather than assuming delivery, and read delivery attempts from
`mcp:list_notification_events` / `mcp:list_delivery_receipts` if the question is
whether somebody was reached. "Sent" and "received" are different answers.

## Boundaries

* Do not invent a contact route, an offer detail or a capability claim to fill a
  gap in a draft.
* Do not treat the absence of a refusal as an approval.
* Do not store a produced draft as the authoritative copy. The server holds the
  artifact; a client holds a rendering of it.
