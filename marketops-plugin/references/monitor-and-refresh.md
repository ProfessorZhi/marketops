# Monitor pool, freshness and refresh

Use when the request is about **what changed**, about re-checking a set of
objects, or about keeping a pool under watch.

## Two different things, and only one of them spends

| | A saved view | A watch |
|---|---|---|
| What it is | a stored query over shared facts and assessments | a rule that turns that query into due work |
| What it costs | nothing — no provider call, no budget, no Run | a permissioned, budgeted write |
| Read it with | `mcp:run_saved_view` | `mcp:list_watches` |
| Change it with | `mcp:put_saved_view` | `mcp:put_watch` |

Saving a view is a read-shaped act. **Enabling a watch is the write that needs
permission and budget.** Never present one as the other: a user who says "save
this list" has not asked for monitoring, and a user who says "keep an eye on
this" has not agreed to a budget.

## Reading a pool

`mcp:run_saved_view` returns the rows of a stored query with no provider call and
no Run. One organisation appearing in three views is one organisation read three
times — never three rows to add up. If a query cannot be evaluated, the server
says **which part** could not be; present that rather than a shorter list, since
a short list and an empty one look identical in a report.

## Enabling monitoring

1. Read what is already enabled with `mcp:list_watches`: target, revision, next
   due moment and the budget envelope it carries.
2. Write or revise with `mcp:put_watch`: the aspects to watch, the freshness and
   trigger policy, the budget envelope and the notification policy.
3. If the target workstream is paused, the write is **refused rather than
   queued** — a watch that is enabled and will never fire must not be able to
   look enabled.

A watch produces a **due request**. It does not run anything itself; the Run that
follows comes through the ordinary admission path. There is exactly one trigger
driver in the deployment, and neither this package nor a client session is it.

## What "nothing changed" means

An empty due check is a real answer and costs nothing. Do not describe it as a
failure, and do not respond to it by widening the refresh. If the user wants more
coverage, that is a scope decision expressed as a new watch revision or a new
Scene version — read the current one first with `mcp:list_watches`.

## Freshness and change signals

* Report staleness as the server reports it, per aspect, rather than as one age
  for the whole object.
* A change signal is not the same as a due moment. Both can produce work, and
  which one did is visible in the outcome; say which one it was.
* Never state that a change was "detected" because time passed.

## Boundaries

* Do not maintain a client-side list of what was last seen in order to compute
  changes — read the server's own event and receipt surface instead
  (`mcp:list_notification_events`, `mcp:list_delivery_receipts`).
* Do not stop or start work by editing local state; pausing is a server use case
  (`mcp:control_workstream`) with an expected revision.
