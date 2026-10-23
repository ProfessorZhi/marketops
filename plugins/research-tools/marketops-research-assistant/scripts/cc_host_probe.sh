#!/bin/sh
# Ask a real Claude Code install whether this plugin's MCP actually loads.
#
# Why this exists: `claude plugin details` reports the MCP *component* and
# `claude mcp list` health-checks the config, and both can look green while a
# real session reports the server `failed` and never launches it. Only a session
# tells the truth, so this script drives one.
#
# What it does NOT do: it never reads, prints, copies or stores a credential. It
# *unsets* the provider variables by name before anything starts, so the managed
# MCP cannot reach a vendor even if the caller's shell had keys. It touches no
# customer account, no device, no system permission and no client setting: HOME,
# XDG_* and TMPDIR are redirected into --workdir, and every network/spend switch
# this plugin reads is pinned to 0.
#
# Usage:
#   cc_host_probe.sh --plugin-dir /abs/path [--workdir /abs/path]
#                    [--python /abs/venv/bin/python] [--session]
#
#   --python    interpreter the plugin's launcher should use. Omit it to see the
#               shipped default; supply one to separate "the launcher found no
#               mcp-capable Python" from "the plugin is broken".
#   --session   also run one real `claude -p` turn that calls a T0 read-only
#               tool. This spends one model turn on the caller's own account and
#               needs the caller's own auth to be reachable; without it the
#               script stays local (metadata + health check only).
set -eu

PLUGIN_DIR=""
WORKDIR=""
PYTHON=""
SESSION=0
while [ $# -gt 0 ]; do
    case "$1" in
        --plugin-dir) PLUGIN_DIR="${2:-}"; shift 2 ;;
        --workdir)    WORKDIR="${2:-}"; shift 2 ;;
        --python)     PYTHON="${2:-}"; shift 2 ;;
        --session)    SESSION=1; shift ;;
        -h|--help)    sed -n '2,26p' "$0"; exit 0 ;;
        *) printf 'unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

[ -n "$PLUGIN_DIR" ] || { printf 'usage: %s --plugin-dir <abs> [--session]\n' "$0" >&2; exit 2; }
[ -d "$PLUGIN_DIR" ] || { printf 'PLUGIN_DIR_NOT_A_DIRECTORY: %s\n' "$PLUGIN_DIR" >&2; exit 2; }
case "$PLUGIN_DIR" in /*) ;; *) printf 'PLUGIN_DIR_MUST_BE_ABSOLUTE\n' >&2; exit 2 ;; esac
command -v claude >/dev/null 2>&1 || { printf 'CLAUDE_CLI_UNAVAILABLE: install Claude Code first\n' >&2; exit 3; }

for name in SERPAPI_API_KEY SERPER_API_KEY CRAWL4AI_API_KEY TINYFISH_API_KEY \
            TYPESAFE_API_KEY TINYFISH_MCP_ACCESS_TOKEN DEEPSEEK_API_KEY SERPER_API_KEY_2 SERPAPI_API_KEY_2; do
    unset "$name" 2>/dev/null || true
done

WORKDIR="${WORKDIR:-$(mktemp -d)}"
mkdir -p "$WORKDIR/home" "$WORKDIR/tmp" "$WORKDIR/cwd" "$WORKDIR/out" "$WORKDIR/data"
HOME="$WORKDIR/home"; export HOME
XDG_CONFIG_HOME="$WORKDIR/home/.config"; export XDG_CONFIG_HOME
XDG_CACHE_HOME="$WORKDIR/home/.cache"; export XDG_CACHE_HOME
XDG_DATA_HOME="$WORKDIR/home/.local/share"; export XDG_DATA_HOME
TMPDIR="$WORKDIR/tmp"; export TMPDIR
MARKETOPS_RESEARCH_DATA_ROOT="$WORKDIR/data"; export MARKETOPS_RESEARCH_DATA_ROOT
MARKETOPS_SEARCH_ALLOW_NETWORK=0; export MARKETOPS_SEARCH_ALLOW_NETWORK
MARKETOPS_SEARCH_MAX_CALLS=0; export MARKETOPS_SEARCH_MAX_CALLS
MARKETOPS_RESEARCH_ALLOW_NETWORK=0; export MARKETOPS_RESEARCH_ALLOW_NETWORK
MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS=0; export MARKETOPS_RESEARCH_MAX_EXTERNAL_CALLS
MARKETOPS_TINYFISH_AGENT_ALLOW_PAID=0; export MARKETOPS_TINYFISH_AGENT_ALLOW_PAID
if [ -n "$PYTHON" ]; then
    MARKETOPS_RESEARCH_PYTHON="$PYTHON"; export MARKETOPS_RESEARCH_PYTHON
fi

# The plugin's name comes from its manifest, not from the directory it happens to
# sit in: a customer who unzips into `marketops-research-assistant-v0.4.0/` is
# still installing the same plugin, and deriving the name from the path made
# `plugin details` fail for exactly that layout.
PLUGIN_NAME="$(basename "$PLUGIN_DIR")"
if command -v python3 >/dev/null 2>&1 && [ -f "$PLUGIN_DIR/.claude-plugin/plugin.json" ]; then
    manifest_name="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("name",""))' \
        "$PLUGIN_DIR/.claude-plugin/plugin.json" 2>/dev/null || true)"
    [ -n "$manifest_name" ] && PLUGIN_NAME="$manifest_name"
fi
cd "$WORKDIR/cwd"

printf '== plugin details (component inventory) ==\n'
rc=0
claude --plugin-dir "$PLUGIN_DIR" plugin details "$PLUGIN_NAME" \
    > "$WORKDIR/out/details.txt" 2>&1 || rc=$?
printf 'exit=%s\n' "$rc"
sed -n '/Component inventory/,/LSP servers/p' "$WORKDIR/out/details.txt" || true

printf '\n== mcp list (config health check) ==\n'
rc=0
claude --plugin-dir "$PLUGIN_DIR" mcp list > "$WORKDIR/out/health.txt" 2>&1 || rc=$?
printf 'exit=%s\n' "$rc"
cat "$WORKDIR/out/health.txt"

if [ "$SESSION" = "1" ]; then
    TOOL="mcp__plugin_${PLUGIN_NAME}_marketops-research__marketops_setup_status"
    printf '\n== real session (one model turn; T0 read-only tool) ==\n'
    rc=0
    claude --plugin-dir "$PLUGIN_DIR" \
        -p "Call the tool \`$TOOL\` exactly once with no arguments, then reply with ONLY its raw JSON result." \
        --output-format stream-json --verbose --allowedTools "$TOOL" \
        < /dev/null > "$WORKDIR/out/session.jsonl" 2>"$WORKDIR/out/session.err" || rc=$?
    printf 'exit=%s\n' "$rc"
    python3 - "$WORKDIR/out/session.jsonl" <<'PY' || true
import json, sys
for line in open(sys.argv[1]):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    if event.get("type") == "system" and event.get("subtype") == "init":
        managed = [s for s in (event.get("mcp_servers") or [])
                   if "marketops" in str(s.get("name"))]
        tools = [t for t in (event.get("tools") or []) if "marketops" in str(t)]
        print("mcp status:", json.dumps(managed))
        print("managed tools visible to the model:", len(tools))
    if event.get("type") == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                print("host issued tools/call:", block.get("name"), json.dumps(block.get("input")))
    if event.get("type") == "user":
        for block in event.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = block.get("content")
                if isinstance(text, list):
                    text = "".join(x.get("text", "") for x in text if isinstance(x, dict))
                print("tool result:", str(text)[:400])
PY
    printf '\nNOTE: a session can report the server `failed` without ever launching\n'
    printf 'it, which the health check above will not show. If the two disagree,\n'
    printf 'believe the session.\n'
fi

printf '\nworkdir: %s\n' "$WORKDIR"
printf 'artifacts: %s/out\n' "$WORKDIR"
