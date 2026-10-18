# WorkBuddy compatibility receipt — `BLOCKED_EXTERNAL`

Measured 2026-09-29. This receipt records **a failure to measure**, which is a
result, and is deliberately not written as a pass.

## What was looked for

| Check | Result |
|---|---|
| an executable named `workbuddy` / `work-buddy` / `WorkBuddy` on `PATH` | not found |
| a `~/.workbuddy`, `~/.config/workbuddy` or `~/.config/WorkBuddy` directory | absent |
| a WorkBuddy entry in this node's client MCP configuration | none |
| a listening endpoint on this node identifying itself as WorkBuddy | none |

The client is not installed on this machine, and no reachable instance of it
exists here. Every instruction below is therefore an untested instruction, and
that is what this file says it is.

## What is therefore not claimed

**Not one field.** Not `mcp_connected`, not `installed_projection`, not
`tool_catalog_verified`. WorkBuddy is expected to speak MCP and that expectation
is the reason for caution rather than for confidence: a client that speaks the
protocol still has its own auth path, its own manifest format, its own session
lifecycle and its own notification behaviour, and none of those is the protocol.

The dangerous version of this file would read *"WorkBuddy: MCP-compatible,
supported."* It would be short, plausible, and worth nothing.

## What would lift it

1. A WorkBuddy client reachable from the machine that runs the verification.
2. The projection installed from `install.md`, then connected.
3. The contract run in the order given in `../README.md`: connect, authenticate
   (with a refused negative control), compare the received tool names against
   the deployment's catalog, then read, write, disconnect, reconnect, and — if
   the client has a push path at all — notify.

If the client turns out not to have a capability, that field becomes
`unsupported` with the reason. The one thing it must not become is `verified`
without the run.

## What was measured in the meantime

The canonical Skill and the connection template in this package are the same
ones the Codex run used, and the Codex receipt records that they hold no
business fact and no credential. A WorkBuddy projection is a copy of that Skill
plus an install note — which is to say the content side of this projection is
already fixed, and only the client half is unmeasured.
