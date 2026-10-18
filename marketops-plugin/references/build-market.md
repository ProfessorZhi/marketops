# Build the pool / identity / responsibility organisations

Use when the request is about **starting a workstream**, adding a market, or
bringing more of a market into scope. Read `SKILL.md` §1 first: the workstream
and the Scene come before anything here.

## What the user is actually asking for

"Research more of this market" is a request for a **bounded launch**, not for an
unbounded crawl. Scope — region, scenario, role strategy, source plan and budget
— lives in the Scene, and a change to any of them is a new version rather than a
field edit. Never widen a request silently; if the request needs a scope the
Scene does not carry, say which cell is missing.

## Order of operations

1. **Read the Scene.** `mcp:get_scene` for the declared version, then
   `mcp:get_effective_scene` for the compiled cells — resolved region scope,
   scenario profile, offer, sales motion and the overridable policy, each with
   its source. A cell with no source is not a default; it is a missing input.
2. **Compile before deciding.** `mcp:preview_effective_scene` compiles a declared
   version into an effective one. It reads only what is already on disk and calls
   no provider. Compile it if the user is about to launch; do not compile it to
   answer a question that a read already answers.
3. **Preview the launch.** `mcp:preview_launch` reports what this version would
   still be missing, who it would wait on, and **one** next action. Present that
   action rather than inventing a checklist.
4. **Start it.** `mcp:start_workstream` binds the approved Scene version, records
   the launch and admits the first Run through the ordinary admission path. It
   returns the workstream, the Run and the launch, plus the queue state and when
   it will wake again.
5. **Only if it is refused**, read the reason rather than the absence.

## Resubmitting is not a second launch

The same workstream, Scene version and idempotency key return **the same
launch**: nothing is rebuilt and no budget is reset. The user saying "try that
again" is therefore safe — but send the *same* key for the same intent. A
different key for the same intent is a genuine second launch, and only the user
can decide that is what they want.

## What belongs where

| The user asks for | Where it lives |
|---|---|
| a different region or a regional subset | the Scene's compiled region scope |
| a different scenario or site type | the Scene's scenario profile |
| a different role or function to target | the Scene's sales motion and role vocabulary |
| a different product or offer | the Scene's product overlay, by approved version |
| more accounts of the same kind | a further launch over the same Scene version |

Nothing in that table is a client-side setting. If the user wants one of them to
change, the output is a Scene version and, where the change is a rule, a proposal
— not a local override.

## Boundaries

* Do not create a pool of accounts by hand and present it as a market.
* Do not treat an uploaded list as a source of truth about organisations; it is
  an input that still has to be admitted through the ordinary path.
* Do not claim the pool grew. The size is read with `mcp:list_account_pool` and
  reported as read.
