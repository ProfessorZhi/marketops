# Running the plugin in Claude Code, Codex and WorkBuddy

The plugin is the same everywhere: one MCP server (`scripts/run.sh`, stdio) and the skills
(`skills/`, `references/`, `agents/`, `commands/`). Each host attaches the server and loads the
skills in its own way; the lead agent then starts workers with the host's own multi-agent mode
(see `skills/marketops-research/SKILL.md`). Install once with `install.sh` (Windows:
`Install.ps1`); it prepares `~/.marketops` (Python, browser, keys) for every host.

| | Claude Code | Codex | WorkBuddy |
|---|---|---|---|
| MCP server | from the plugin (`.mcp.json`) | `~/.codex/config.toml`, below | its MCP settings, below |
| Skills | from the plugin | copy `skills/` (and keep `references/` beside them) to where your Codex loads skills, or point `AGENTS.md` at `skills/marketops-research/SKILL.md` | load `skills/` as its skills / knowledge; same files |
| Workers | `marketops-worker` subagents (`agents/`) | Codex sub-agents if your version has them, else extra sessions: `codex exec "<start line>"` | its multi-agent mode, else one thread per worker |
| Status | verified (`claude plugin validate`) | to be verified on a real install | to be verified on a real install |

## Keys (all hosts)

Every provider is optional; the plugin steps down when one is missing (`project()["capabilities"]`).
Several keys per provider are allowed: `SERPER_API_KEY`, `SERPER_API_KEY_2` ... `_9`, or several
in one value separated by commas. A key that runs out or is refused hands over to the next by
itself. Keys are read from `~/.marketops/keys.env` and, if set, from the file
`MARKETOPS_KEYS_FILE` points to (a vault file works) - both are read again when they change, so
adding or replacing a key needs no restart. Slots add money and speed only when the keys belong to different accounts:
providers such as DeepSeek count balance and rate per account.

## Claude Code

```
/plugin marketplace add ProfessorZhi/marketops
/plugin install marketops-research-assistant@marketops
```

## Codex

Add the server to `~/.codex/config.toml` (`install.sh` prints this block with your path):

```toml
[mcp_servers.marketops]
command = "sh"
args = ["/ABSOLUTE/PATH/TO/marketops-research-assistant/scripts/run.sh"]
tool_timeout_sec = 120
```

Long work runs in background processes the server starts (`run`, `run(orgs=[...])`), so most
tools return in seconds; 120 s leaves room for `fetch` of a slow page. `grade(project)` grades in
the foreground: on a host with a short tool timeout give it `limit=200` and call it again (the
running sweep grades in the background anyway).

## WorkBuddy

Add an MCP server of type stdio with the same command (`sh` and the absolute path of
`scripts/run.sh`) in WorkBuddy's MCP settings, using the form its documentation gives. Load the
`skills/` folder as skills. Record on the first run: the WorkBuddy version, whether the tools
list shows the 11 marketops tools, whether `project("smoke")` answers, and how its multi-agent
mode starts a worker with a start line.

## Start line for a worker (any host)

```
You are the <sources|pool|websites|dig|finish> worker on MarketOps project "<project>".
Read references/roles/<role>.md of the marketops plugin and work until your queue is empty.
```
