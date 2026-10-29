#!/bin/sh
# MarketOps installer (Linux / macOS): a private Python, the local browser, your keys, and one
# launcher every host uses the same way (Claude Code, Codex, WorkBuddy, any MCP host).
# Re-run any time - after each plugin update too: it keeps what is there and only asks for keys
# that are missing.
set -eu
# the folder this script is in, however it was called (sh install.sh, sh ./x/install.sh, a full
# path): 0.8.0's "${0%/*}" was the file name itself for "sh install.sh", and nothing was installed
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || { echo "cannot find the installer's folder" >&2; exit 1; }
[ -f "$here/scripts/server.py" ] || { echo "scripts/server.py is not beside $0: run the installer inside the unzipped plugin" >&2; exit 1; }
home="${MARKETOPS_HOME:-$HOME/.marketops}"
mkdir -p "$home"

py=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        py=$(command -v "$c"); break
    fi
done
[ -n "$py" ] || { echo "Python 3.10 or newer is needed (python.org)."; exit 1; }

echo "1/4 Python environment in $home/venv"
[ -x "$home/venv/bin/python" ] || "$py" -m venv "$home/venv"
"$home/venv/bin/python" -m pip install -q --upgrade pip
"$home/venv/bin/python" -m pip install -q -r "$here/requirements.txt"

echo "2/4 Local browser for Crawl4AI"
"$home/venv/bin/python" -m playwright install chromium >/dev/null || {
    echo "  Chromium did not install; on Linux try: $home/venv/bin/python -m playwright install --with-deps chromium"; }

echo "3/4 Provider keys (saved in $home/keys.env, readable only by you; Enter skips one)"
keys="$home/keys.env"
touch "$keys"; chmod 600 "$keys"
for pair in "SERPER_API_KEY:Serper" "SERPAPI_API_KEY:SerpApi" "DEEPSEEK_API_KEY:DeepSeek" \
            "TINYFISH_API_KEY:TinyFish" "TYPESAFE_API_KEY:Jev (TypeSafe)"; do
    name=${pair%%:*}; label=${pair#*:}
    if grep -q "^$name=." "$keys" 2>/dev/null; then echo "  $label: kept"; continue; fi
    # an agent installing for the user never types keys: they go into keys.env by hand
    if [ -n "${MARKETOPS_NO_PROMPT:-}" ]; then echo "  $label: missing - add $name=... to $keys"; continue; fi
    printf '  %s key: ' "$label"
    stty -echo 2>/dev/null || true; read -r value || value=""; stty echo 2>/dev/null || true; echo
    [ -n "$value" ] && printf '%s=%s\n' "$name" "$value" >> "$keys"
done

echo "4/4 The plugin in $home/plugin and its launcher $home/bin/marketops-mcp"
# the whole plugin, not only the scripts: the server hands its skills and role files to every
# host through guide(), and reads them from beside itself
rm -rf "$home/plugin.new"; mkdir -p "$home/plugin.new" "$home/bin"
for item in .claude-plugin scripts skills references agents commands tools requirements.txt HOSTS.md README.md; do
    [ -e "$here/$item" ] && cp -R "$here/$item" "$home/plugin.new/"
done
rm -rf "$home/plugin"; mv "$home/plugin.new" "$home/plugin"
cat > "$home/bin/marketops-mcp" <<EOF
#!/bin/sh
exec "$home/venv/bin/python" "$home/plugin/scripts/server.py" "\$@"
EOF
chmod 755 "$home/bin/marketops-mcp"
"$home/bin/marketops-mcp" --check

launcher="$home/bin/marketops-mcp"
if command -v codex >/dev/null 2>&1; then
    codex mcp remove marketops >/dev/null 2>&1 || true
    codex mcp add marketops -- "$launcher" >/dev/null 2>&1 && echo "Codex: registered (codex mcp list)" || true
fi
cat <<EOF

The same server for every host - command: $launcher (no arguments, stdio)
  Claude Code: /plugin marketplace add ProfessorZhi/marketops
               /plugin install marketops-research-assistant@marketops
  Codex (~/.codex/config.toml, if not registered above):
      [mcp_servers.marketops]
      command = "$launcher"
  WorkBuddy, Claude Desktop, Cursor and any MCP host (JSON):
      {"mcpServers": {"marketops": {"command": "$launcher"}}}
Then ask the agent to call guide() - every host reads the same workflow from it.
EOF
