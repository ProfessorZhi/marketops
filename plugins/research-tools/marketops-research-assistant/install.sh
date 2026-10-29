#!/bin/sh
# MarketOps installer (Linux / macOS): a private Python, your keys, one launcher every host uses
# the same way (Claude Code, Codex, WorkBuddy, any MCP host), then the local browser reader.
# Re-run any time - after each plugin update too: it keeps what is there and only asks for keys
# that are missing.
#   MARKETOPS_NO_PROMPT=1  an agent installs: no key is asked for, other hosts are left alone
#   MARKETOPS_LITE=1       skip the local browser reader (about 1 GB); the plugin works without it
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

echo "1/5 Network"
# a broken local proxy and a slow PyPI cost the 0.8.2 Windows install half an hour: test both first
net=$("$py" "$here/tools/install_helper.py" net 2>/dev/null || echo '{}')
field() { printf '%s' "$net" | "$py" -c "import json,sys; print(json.load(sys.stdin).get('$1', ''))"; }
if [ "$net" = "{}" ]; then
    echo "  could not test the network; going on with the computer's own settings"
elif [ "$(field drop_proxy)" = "True" ]; then
    echo "  the proxy does not answer; installing over a direct connection"
    unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy
    export NO_PROXY="*" no_proxy="*"
elif [ -z "$(field pypi_seconds)" ] || [ "$(field pypi_seconds)" = "None" ]; then
    echo "  PyPI does not answer, with or without the proxy: the downloads below may fail"
else
    echo "  PyPI answers in $(field pypi_seconds) s"
fi
mirror=$(field pip_index)
if [ -n "$mirror" ]; then
    echo "  PyPI is slow from here; using the mirror $mirror"
    export PIP_INDEX_URL="$mirror"
    export PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright}"
fi

echo "2/5 Python environment in $home/venv (the core: a minute or two)"
[ -x "$home/venv/bin/python" ] || "$py" -m venv "$home/venv"
"$home/venv/bin/python" -m pip install -q --upgrade pip
grep -v -i '^crawl4ai' "$here/requirements.txt" > "$home/requirements-core.txt"
"$home/venv/bin/python" -m pip install -q -r "$home/requirements-core.txt"

echo "3/5 Provider keys (saved in $home/keys.env, readable only by you; Enter skips one)"
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

echo "4/5 The plugin in $home/plugin and its launcher $home/bin/marketops-mcp"
# the whole plugin, not only the scripts: the server hands its skills and role files to every
# host through guide(), and reads them from beside itself
rm -rf "$home/plugin.new"; mkdir -p "$home/plugin.new" "$home/bin"
for item in .claude-plugin scripts skills references agents commands tools requirements.txt HOSTS.md README.md; do
    [ -e "$here/$item" ] && cp -R "$here/$item" "$home/plugin.new/"
done
rm -rf "$home/plugin"; mv "$home/plugin.new" "$home/plugin"
cat > "$home/bin/marketops-mcp" <<LAUNCHER
#!/bin/sh
exec "$home/venv/bin/python" "$home/plugin/scripts/server.py" "\$@"
LAUNCHER
chmod 755 "$home/bin/marketops-mcp"
"$home/bin/marketops-mcp" --check

launcher="$home/bin/marketops-mcp"
# an agent installing for one host (MARKETOPS_NO_PROMPT) leaves the other hosts alone
if [ -z "${MARKETOPS_NO_PROMPT:-}" ] && command -v codex >/dev/null 2>&1; then
    codex mcp remove marketops >/dev/null 2>&1 || true
    codex mcp add marketops -- "$launcher" >/dev/null 2>&1 && echo "Codex: registered (codex mcp list)" || true
fi
cat <<CONFIG

The same server for every host - command: $launcher (no arguments, stdio)
  Claude Code: /plugin marketplace add ProfessorZhi/marketops
               /plugin install marketops-research-assistant@marketops
  Codex (~/.codex/config.toml, if not registered above):
      [mcp_servers.marketops]
      command = "$launcher"
  WorkBuddy, Claude Desktop, Cursor and any MCP host (JSON):
      {"mcpServers": {"marketops": {"command": "$launcher"}}}
Then ask the agent to call guide() - every host reads the same workflow from it.
MarketOps works from here on; step 5 adds the local browser reader.
CONFIG

# Crawl4AI and its browser are most of the download (about 1 GB). The plugin works without them
# (TinyFish, DeepSeek and plain HTTP read pages), so they come last and their failure stops nothing.
if [ -n "${MARKETOPS_LITE:-}" ]; then
    echo "5/5 Local browser reader: skipped (MARKETOPS_LITE); run the installer again without it to add it"
    exit 0
fi
echo "5/5 Local browser reader (Crawl4AI, about 1 GB; the plugin already works without it)"
if "$home/venv/bin/python" -m pip install -q -r "$here/requirements.txt"; then
    # Chrome or Edge already on this computer does the job of a 150-300 MB Chromium download
    found=$(MARKETOPS_HOME="$home" "$home/venv/bin/python" "$here/tools/install_helper.py" browser 2>/dev/null || true)
    if [ -z "$found" ]; then
        "$home/venv/bin/python" -m playwright install chromium >/dev/null 2>&1 || \
            echo "  Chromium did not install; on Linux try: $home/venv/bin/python -m playwright install --with-deps chromium"
        found=$(MARKETOPS_HOME="$home" "$home/venv/bin/python" "$here/tools/install_helper.py" browser 2>/dev/null || true)
    fi
    echo "  browser: ${found:-none - pages are read by TinyFish, DeepSeek and plain HTTP}"
else
    echo "  Crawl4AI did not install; MarketOps works without it - run the installer again later to add it"
fi
"$home/bin/marketops-mcp" --check | sed -n '3p'
