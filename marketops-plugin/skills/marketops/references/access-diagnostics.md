# Access diagnostics: what is blocked, and why

Use when the request is about **something not working**: a page that will not
load, a challenge, a source that stopped returning, or work that will not start.

## Diagnose from the server's record, never from the symptom

A failed fetch is a recorded attempt, not a mood. Read
`mcp:list_fetch_attempts` for the attempts on a source: what was requested, when,
what came back, and the classified reason. Present the reason the server
classified, not a guess from the status code — a challenge and a rate limit look
alike from outside and are not the same problem.

Then read `mcp:get_capabilities` for whether the deployment can currently do this
at all: each integration reports configured / available at runtime / when it was
last really verified, with a redacted blocking reason. "Not configured here" and
"configured but failing now" are different answers and must be reported
differently.

`mcp:fetch_document` retrieves one document through the ordinary path. A refusal
is an answer with a reason; report it rather than retrying with a different
spelling of the same request.

## Escalating a block

A block that the record classifies as needing a person is not a bug to route
around. Say what is blocked, what was tried (from the attempts), and what the
classifier says is needed. Do not:

* switch to a different source to produce the same fact and present it as if the
  first source had worked;
* describe a blocked source as empty — an empty pool and an unreadable source
  look identical in a report and are not the same thing;
* state that a source is permanently gone because it failed today.

## Long-running work

If the question is whether a job is progressing, read `mcp:get_job` for its
state and the usage it has cost, and `mcp:get_continuation_status` where the work
continues across a session boundary. A disconnected client does not stop durable
work, and a reconnected client does not resume it by asking. Progress comes from
the job, never from the age of the last thing the session saw.

## Boundaries

* Do not claim a capability because the deployment lists it. Listing and being
  executable are different; `mcp:get_capabilities` reports both and so does the
  read that answers "may I dispatch now" (`mcp:get_workstream`).
* Do not report a redacted blocking reason as the absence of one.
* Do not retry a refused write by changing only the identifier. A refusal is
  about the request, not the key.
