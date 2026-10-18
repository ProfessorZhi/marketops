# Codex compatibility receipt

Measured 2026-09-29. What was run, what came back, and — the part that matters —
what was **not** run.

## What was under test

| | |
|---|---|
| client | `codex-cli 0.153.4` (client info `codex-mcp-client` / `Codex` / `0.153.4`) |
| transport | Streamable HTTP, MCP protocol version `2025-06-18` |
| server | a MarketOps MCP server started for this test on an unused loopback port, with a dedicated data root |
| isolation | the client ran against an isolated `CODEX_HOME` (verified: a fresh one reports no MCP servers configured) |
| capture | a byte-level TCP relay between the client and the server |

The capture is the receipt. The relay replaced the credential with a fixed
placeholder before any byte reached disk; the credential value occurs **zero**
times in the captured stream.

## `mcp-connected`

The client's own session opened and completed, in this order:

1. `POST /mcp` — `initialize`, `protocolVersion: 2025-06-18`, client capabilities
   `{"elicitation":{"form":{},"url":{}}}` → `200 OK`
2. `POST /mcp` — `notifications/initialized` → `202 Accepted`
3. `GET /mcp` — the server-sent-events stream → `200 OK`
4. `POST /mcp` — `tools/list` → `200 OK`
5. `DELETE /mcp` — session closed

So the client reached the server, negotiated a protocol version, and received a
tool catalog. That is the whole of the claim.

## `auth-verified`

The same client, pointed at the same server, was then run with a deliberately
wrong credential. The server answered `401 Unauthorized`, no session id was
issued, and no tool list was returned. The negative control is the evidence: a
green light that cannot go red is not an authentication result.

One deployment property showed up here and is worth recording, because it looks
like a fault and is not: the token's audience is the resource identifier the
server derives from its bind address. A client addressing a different address or
port is refused by design.

## `tool-catalog-verified`

The client received **86** tool names. The comparison against the deployment's
catalog was done in both directions:

| | |
|---|---|
| names the client received that the catalog does not contain | **0** |
| catalog names the client did not receive | **11** |
| catalog total / served | 97 / 86 |

The 11 withheld, with the reason for each:

| count | role in the catalog | why absent |
|---|---|---|
| 6 | researcher | the session's role does not intersect theirs |
| 2 | authorizer | same |
| 3 | controller | they are the wave/outreach group, and this deployment has no mail transport, so the server itself withholds them rather than registering an entry with no capability behind it |

The role census reconciles exactly: 85 controller-role names minus those 3, plus
4 names whose role set is shared, is 86. Nothing was asserted here that the
arithmetic does not close.

**A count is not the check.** The check is the name-set difference in both
directions. `86` on its own would have been satisfied by a server that returned
86 of the wrong tools.

## `plugin-install-verified`

The client's own plugin path was exercised against an isolated client home, and
the package is what the client says it installed:

| step | result |
|---|---|
| `codex plugin marketplace add <package dir>` | `Added marketplace \`marketops-local\`` |
| `codex plugin list` | `marketops@marketops-local  installed, enabled  0.1.0` |
| `codex plugin add marketops@marketops-local` | installed into the client's own plugin cache under version `0.1.0` |
| `diff -r <source> <installed>` | no output, exit `0` |
| file-by-file sha256, source vs installed | `files=31  mismatches=0` |

The identity comparison is the load-bearing part. "The client said installed" is
a statement about a directory existing; the comparison is a statement about what
is inside it, and only the second one survives a package that half-copies.

Two things this does **not** say:

* It does not say a session read the Skill from the installed location. That is
  `installed_projection`, and it stays `not_verified`.
* It does not say the plugin's own MCP entry connected. The connection above was
  measured through the client's **direct** MCP configuration. The plugin ships
  its endpoint as a placeholder precisely because a package that carries a live
  endpoint is a package that carries a deployment's configuration — so this
  half is filled in per deployment and was not filled in for this run.

One deployment note that falls out of the same fact: this install is a **copy**.
The client holds the revision it installed, and it will not follow the source
afterward — including if the source is later corrected. That is a property of
this client's install path, not of this package, and it is the reason the
post-install comparison above is worth doing at all.

## What was not run, and why

| field | why not |
|---|---|
| `read_flow_verified`, `write_flow_verified` | the client's model turn could not complete in this environment (its own upstream provider refused the isolated credential), so the client never really invoked a tool. The MCP handshake had already completed when the turn failed — which is why the handshake is claimed and the flows are not |
| `durable_job_disconnect_verified`, `reconnect_verified`, `multi_session_isolation_verified` | not attempted; each needs a running model turn to drive it |
| `notification_pull_verified`, `notification_push_verified` | not attempted; a handshake does not answer a notification-capability question |

Every one of those is a real gap. None is a refusal of the client: they are
things this slice did not get to measure, and the difference between "the client
cannot" and "we did not look" is the reason they are `not_verified` rather than
`unsupported`.

## Teardown

The test server and the relay were both stopped, and the scratch environment was
removed. A pre-existing listener on another port, which this test did not own,
was left untouched. No credential was left outside its own store.
