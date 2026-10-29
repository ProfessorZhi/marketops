# One plugin on every host and every computer

The plugin works the same in Claude Code, Codex, WorkBuddy or any MCP host, on Linux, macOS and
Windows, because everything that decides how a run goes travels with the MCP server itself:

| What | Where it comes from - the same on every host |
|---|---|
| The rules of a run (brief with `minutes`, claim after every done, end only on `lane_finished` / `stop`, close out before the report) | the server's MCP handshake (`instructions`) - every host gives them to its agent |
| The workflow, the tools, the business knowledge, every role | `guide(topic)`: the same text Claude Code loads as skills, read by any agent that can call a tool |
| What to do next | every tool answer: `next`, `why_empty`, `needs_you`, `role_guide` |
| One agent or many | many: one worker per lane with the host's multi-agent mode; one: `tasks(action="claim", kind="any")` in a loop runs the same pipeline, lane after lane |
| Keys, projects, cache | `~/.marketops` (Windows `%USERPROFILE%\.marketops`) - the same files whatever host calls |

Skills loaded by the host (Claude Code's `skills/`) are a convenience on top; nothing depends on
them.

## Install (once per computer, again after each update)

Unzip the release, then run the installer inside the unzipped folder:

- Linux / macOS: `sh install.sh`
- Windows (PowerShell): `.\Install.ps1`

An agent installing for you sets `MARKETOPS_NO_PROMPT=1`: the installer then asks for no key and
lists the missing ones, to add to `keys.env` by hand. Otherwise it prepares a private Python and
the local browser, asks for the keys that are missing, copies
the whole plugin to `~/.marketops/plugin` and writes one launcher:

- Linux / macOS: `~/.marketops/bin/marketops-mcp`
- Windows: `%USERPROFILE%\.marketops\bin\marketops-mcp.cmd`

It ends with `--check` (version, plugin folder, keys file, Python, which providers work) and
prints the configuration below with your own path.

## Attach the server

Every host: an MCP server of type stdio whose command is the launcher, with no arguments.

| Host | How |
|---|---|
| Claude Code | `/plugin marketplace add ProfessorZhi/marketops`, `/plugin install marketops-research-assistant@marketops` (on Windows the installer also registers the launcher with `claude mcp add`) |
| Codex | registered by the installer when `codex` is installed; else `~/.codex/config.toml`: `[mcp_servers.marketops]` `command = "<launcher>"` `tool_timeout_sec = 120` |
| WorkBuddy, Claude Desktop, Cursor, any MCP host | its MCP settings, JSON `{"mcpServers": {"marketops": {"command": "<launcher>"}}}` |

Long work runs in background processes the server starts, so most tools return in seconds; give
the host a tool timeout of at least 120 s for `fetch` of a slow page.

## Check a host (2 minutes, the same on every host)

1. The tool list shows `guide`, `project`, `tasks`, `fetch`, `grade`, `export` and the others.
2. `guide()` answers with the version and the topics; `project("smoke")["version"]` is the
   release you installed.
3. Ask the agent what `guide("workflow")` says to do first - it should name the brief with
   `minutes` and starting the team.
4. Note whether the host can run several agents at once; if not, the lead works alone with
   `kind="any"`.

The same check runs in CI on Linux, macOS and Windows (`tests/test_081_portability.py`).

## Keys (all hosts)

Every provider is optional; the plugin steps down when one is missing (`project()["capabilities"]`).
Several keys per provider are allowed: `SERPER_API_KEY`, `SERPER_API_KEY_2` ... `_N` for any N
(no upper bound), or several in one value separated by commas. A key that runs out or is refused
hands over to the next by itself. Keys are read from `~/.marketops/keys.env` and, if set, from the
file `MARKETOPS_KEYS_FILE` points to (a vault file works) - both are read again when they change,
so adding or replacing a key needs no restart. Slots add money and speed only when the keys
belong to different accounts: providers such as DeepSeek count balance and rate per account.

## Start line for a worker (any host)

```
You are the <sources|pool|websites|research|dig|finish> worker on MarketOps project "<project>".
Call guide("worker") and guide("role:<role>") of the marketops tools and work until a claim says
lane_finished or stop - after every done, claim again; never end your turn on an empty claim.
```

One agent alone: `You lead MarketOps project "<project>" alone. Call guide("workflow"), write the
brief with minutes, then claim tasks(action="claim", kind="any", wait=50) in a loop until a claim
says lane_finished or stop; close out and report.`
