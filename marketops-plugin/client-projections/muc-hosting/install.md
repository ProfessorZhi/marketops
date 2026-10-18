# MUC/Hosting projection — install and connect

**Status: unverified.** No instance of this client is reachable from the machine
where this package was written. Nothing below has been run, and `RECEIPT.md`
records what was looked for and not found.

## Install the Skill half

The same canonical Skill as every other client: `../../SKILL.md` and
`../../references/`, copied under a `marketops/` directory in wherever this
client loads skills from. A projection changes installation, manifest, display,
the optional session bridge and notification ability — never business content.

## Connect

Two fields, as for every MCP client: the deployment's Streamable HTTP endpoint,
and the credential supplied by reference through the client's own managed
configuration. `mcp-server-entry.unverified.json` carries the common
`mcpServers` shape and is named that way because this client's configuration
format has not been observed here; its own documentation is the authority.

Keep the audience in mind: the token's audience is the resource identifier the
server derives from its bind address, so the endpoint the client uses must be
the endpoint the deployment sees.

## Run the contract in the order given in `../README.md`

Every field in `status.json` becomes measurable the same way it was for Codex:
connect, authenticate against a refused negative control, compare received tool
names against the deployment's catalog in both directions, then read, write,
disconnect, reconnect, notify.

## The three things this entry point specifically has to answer

These are on top of the shared contract, and they are the reason MUC/Hosting is
in the first client set rather than an afterthought:

1. **The principal mapping.** Who is calling, expressed in this deployment's own
   terms. Without it an authentication result is uninterpretable: a session that
   opens has not been shown to be *anybody's*. Establish the mapping before
   reading anything into a green light.
2. **The policy decision path.** This is an entry point where a person may be
   asked to decide something. Exercise it and observe the outcome — including
   the case where the answer is no, or where the person is not entitled to
   decide. A decision path validated only on the happy path is not validated.
3. **Notification pull.** Events and their delivery receipts, read and shown.
   Push is a separate and optional claim; if this platform has no push path,
   that is `unsupported` and the ladder in `../README.md` applies.

## One boundary that is easy to cross here

If the platform has a scheduler, it may call the same fire entry point with the
same fire identifier as every other driver. That is a supported integration —
and it is **not** a second trigger driver, and it is **not** a verified client
contract. A deployment has exactly one trigger driver
(`../../references/monitor-and-refresh.md`); a host that calls one use case has
not become it, and must not be made into one to make this projection look more
capable than it is.
