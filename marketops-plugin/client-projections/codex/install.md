# Codex projection — install and connect

Codex reads this package in two independent halves. They fail separately, and
conflating them is the usual mistake:

* the **Skill** half — the workflow text, loaded from the plugin;
* the **connection** half — the MCP endpoint, resolved by the client's own
  configuration.

A green light on one says nothing about the other. `RECEIPT.md` records them as
separate claims for that reason.

## Connect

The connection is two fields. `mcp-server-entry.toml` is the template; the value
shape was measured against this client (`codex mcp add` / `codex mcp get --json`).

1. Set `url` to your deployment's Streamable HTTP endpoint.
2. Set `bearer_token_env_var` to the **name** of the environment variable the
   client will read the credential from. The value is not part of this package,
   and how your deployment issues one is not described here.
3. Confirm the endpoint is the one the client will actually use. The token's
   audience is the resource identifier the server derives from its bind address,
   so a client reaching it by a different address or port is refused — by
   design, not by misconfiguration.

Then check the connection the only way that means anything: open a session and
read the tool list. Reading the configuration back tells you what you typed, not
what the server answered.

### If the session does not open

| What you see | What it is |
|---|---|
| refused before any tool list | the credential was not accepted — check the audience/property above before anything else |
| connects, no tools | the endpoint is a server, not necessarily this one; compare the names, not the count |
| connects, fewer tools than you expected | expected: the client sees the intersection of the catalog with its session's role, and a deployment with no certain capability withholds those entries rather than listing them |

## Install the Skill half

The plugin manifest points `skills` at the package's `skills/` directory, and
`mcpServers` at the connection entry. Installing is the client's own plugin
install path — there is no bespoke installer here, and no post-install script
that reaches outside the package.

The Skill itself needs no configuration: it holds no endpoint, no identifier and
no business fact. Everything it routes to is read from the server at the moment
of use.

Two operational facts about that path, both measured (see `RECEIPT.md`):

* the plugin ships its endpoint as a **placeholder**. Install it, then fill the
  endpoint in for your deployment. A package that carried a live endpoint would
  be carrying one deployment's configuration.
* the install is a **copy**. The client holds the revision it installed and will
  not follow the source afterwards — including after a correction. Reinstall to
  pick one up.

## What this projection deliberately does not carry

* **No credential**, no credential-shaped value, and no description of where one
  is kept or how it is obtained.
* **No business fact** — no account, no count, no budget, no permission claim.
  The Skill's own §0 is the rule; it applies to these files too.
* **No client-side store.** Codex has session state of its own, and none of it
  is a business fact. Nothing here writes a copy of server state for later
  reading.
