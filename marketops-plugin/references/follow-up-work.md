# Follow-up pool, review and human decisions

Use when the request is about **what happens next**: an item someone owes, a case
waiting on a person, or an outcome that has to be settled by a human.

## The follow-up pool is a projection, not a tag

The next step for an object is **durable work**, read with
`mcp:list_action_items`: the subject, the action type, the owner, the due moment,
the state, what blocks it and the evidence it rests on. It is not a per-object
status field, and reading it changes nothing.

A subject may carry only one **open** item of a given kind. If the user believes
there should be several, that is a signal that the earlier one was never
resolved, not a reason to create a second.

## Settling an item

`mcp:resolve_action_item` moves one item to done or dismissed, or reassigns it,
and takes the version the caller last read:

* a replay of the same resolution is idempotent;
* a stale version is **refused** rather than allowed to overwrite newer facts.

So: read first, then resolve with the version that was read. If the resolve is
refused, re-read and show the user what changed — do not retry blindly.

## Cases that need a person

`mcp:list_review_cases` and `mcp:get_review_flow` show the cases that are waiting
on a human, with the question being asked. Two rules:

* **A case is not an approval.** Present the case and its question; do not
  present a recommendation as if a decision had been taken.
* **An approval is one specific use case with its own role**, and a different
  person from the one who proposed. This package has no path that performs one;
  if the user asks for it, describe the case and let the server's own flow
  decide who may act.

## Reading a settled outcome

`mcp:get_decision` reads back a judgement that was already made, verbatim, with
the provider and the usage it cost. Replaying it never pays twice. Never describe
what a decision *would* be — read the record.

## Reporting across clients

Every client reads the same items and the same cases, so two clients may present
them differently but cannot disagree about them. A count shown to the user comes
from the same read that produced the rows.

## Boundaries

* Do not keep unresolved work in a client-side list. An item that is not in the
  server's pool does not exist, and one that exists only locally will be
  duplicated the next time the session opens.
* Do not close an item because the conversation moved on.
* Do not summarise a case into a verdict.
