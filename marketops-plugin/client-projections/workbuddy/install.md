# WorkBuddy projection — install and connect

**Status: unverified.** WorkBuddy is not installed on the machine where this
package was written, so none of the steps below has been run. They are written
down so that the first run is a measurement rather than an improvisation — see
`RECEIPT.md`.

## What a projection is, and what it is not

A projection is the **same canonical Skill** plus a client's install and
connection notes. The workflow text does not change between clients; only
installation, manifest, display, the optional session bridge and notification
ability do. If you find yourself editing business content to make a client work,
that is a sign the client is being given authority it should not have.

## Install the Skill half

Copy `../../SKILL.md` and `../../references/` into wherever this client loads
skills from, under a `marketops/` directory. That is the whole content of the
projection. It needs no configuration: it names no endpoint, holds no
identifier, and carries no business fact — every fact it routes to is read from
the server at the moment of use.

The one thing to check after installing: the client really loaded it. A skill
that failed to load is indistinguishable from a skill that found nothing to say.

## Connect

Two fields, the same two every MCP client needs:

| field | value |
|---|---|
| the endpoint | your deployment's Streamable HTTP endpoint, e.g. `http://127.0.0.1:<port>/mcp` |
| the credential | supplied through the client's own managed configuration — by reference, never inline in this package |

`mcp-server-entry.unverified.json` carries the common `mcpServers` shape. It is
named `unverified` on purpose: **this client's configuration format has not been
observed**, so the client's own documentation is the authority for how the entry
is spelled. The two facts it has to express are the two in the table.

Do not put a credential value in a file that lives with the Skill. There is no
step in this projection that requires it, and a projection that needs one has a
design problem rather than a configuration problem.

### Keep the audience in mind

The token's audience is the resource identifier the server derives from its bind
address. A client reaching the endpoint by a different address or port is
refused — by design. Point the client at exactly the endpoint the deployment
will see.

## First run: what to record

Run in this order and write down each answer, because each one is a field in
`status.json`:

1. **Connect.** Does a session open? Record the client's version and the
   protocol version it negotiated.
2. **Authenticate.** Then do it again with a deliberately wrong credential and
   confirm it is refused. A green light that cannot go red is not an
   authentication result.
3. **Compare the tool names.** Not the count — the names, against the
   deployment's catalog, in both directions. Expect fewer than the catalog: the
   client sees the intersection with its session's role, and a deployment
   without a given capability withholds those entries instead of listing them
   with nothing behind them.
4. **Read.** Invoke a read use case and show its result.
5. **Write.** Invoke a write use case and observe its permission and budget
   outcome — not just that it returned.
6. **Disconnect and reconnect.** With durable work running, close the client,
   then read the same state back from a new session.
7. **Two sessions.** Confirm they do not see each other's scope.
8. **Notifications.** Pull an event and its delivery receipts. If this client
   has no push path, stop here and say so — that is `unsupported`, and the
   degradation ladder in `../README.md` applies: read, then write, then durable
   job, then notify.

If the client stops partway down that ladder, the projection says where it
stopped. It does not describe a lower rung as the rung above it.
