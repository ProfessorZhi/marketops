# MUC/Hosting compatibility receipt — `BLOCKED_EXTERNAL`

Measured 2026-09-29. This receipt records **a failure to measure**, which is a
result, and is deliberately not written as a pass.

## What was looked for

| Check | Result |
|---|---|
| an executable named `muc` / `muc-hosting` on `PATH` | not found |
| a `~/.muc` or `~/.config/muc` directory | absent |
| a MUC/Hosting entry in this node's client MCP configuration | none |
| a reachable hosting endpoint declared in this node's client configuration | none |

No instance is reachable from this machine, so no part of the contract could be
exercised.

## The specific reason this one needs care

MUC/Hosting is a **designed** long-term entry point. The design document says so
explicitly, and says in the same breath that it is not a client with historical
use behind it. A design document is a good source for what to *test* and a bad
source for what has been *measured*, and the two are one careless copy-paste
apart. So: the design lists the capabilities this entry point is supposed to
have — a principal mapping, the policy decision, notification pull, reconnect.
This file claims none of them.

## What is therefore not claimed

Not one field. In particular, not the two things this entry point exists for:
the policy decision path and the notification pull path. A design that says a
component will decide policy is not evidence that any principal has ever been
resolved through it.

## What would lift it

1. A MUC/Hosting instance reachable from the machine that runs the verification.
2. A way to establish the principal mapping for it — without which an
   authentication result is uninterpretable, because "who is calling" is the
   thing being mapped.
3. The same contract as the other clients, plus the two claims specific to this
   one: a policy decision, and a notification pull with its delivery receipts.

Then, field by field, `blocked_external` becomes `verified` or `unsupported` on
evidence.

## One thing worth separating while this stays blocked

If the platform has a scheduler, it may call the same fire entry point with the
same fire identifier as every other driver. That is a supported integration and
it is a **different** thing from the client contract above — a cron entry calling
one use case does not make MUC/Hosting a verified client, and a verified client
does not authorise a second trigger driver. `../../references/monitor-and-refresh.md`
states the rule: there is exactly one trigger driver in a deployment.
