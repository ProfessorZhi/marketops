#!/bin/sh
# MarketOps installer (Linux / macOS): a private Python, the local browser, and your keys.
# Re-run any time: it keeps what is there and only asks for keys that are missing.
set -eu
here=$(CDPATH= cd "${0%/*}" && pwd)
home="${MARKETOPS_HOME:-$HOME/.marketops}"
mkdir -p "$home"

py=""
for c in python3.12 python3.13 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        py=$(command -v "$c"); break
    fi
done
[ -n "$py" ] || { echo "Python 3.10 or newer is needed (python.org)."; exit 1; }

echo "1/3 Python environment in $home/venv"
[ -x "$home/venv/bin/python" ] || "$py" -m venv "$home/venv"
"$home/venv/bin/python" -m pip install -q --upgrade pip
"$home/venv/bin/python" -m pip install -q -r "$here/requirements.txt"

echo "2/3 Local browser for Crawl4AI"
"$home/venv/bin/python" -m playwright install chromium >/dev/null || {
    echo "  Chromium did not install; on Linux try: $home/venv/bin/python -m playwright install --with-deps chromium"; }

echo "3/3 Provider keys (saved in $home/keys.env, readable only by you; Enter skips one)"
keys="$home/keys.env"
touch "$keys"; chmod 600 "$keys"
for pair in "SERPER_API_KEY:Serper" "SERPAPI_API_KEY:SerpApi" "DEEPSEEK_API_KEY:DeepSeek" \
            "TINYFISH_API_KEY:TinyFish" "TYPESAFE_API_KEY:Jev (TypeSafe)"; do
    name=${pair%%:*}; label=${pair#*:}
    if grep -q "^$name=." "$keys" 2>/dev/null; then echo "  $label: kept"; continue; fi
    printf '  %s key: ' "$label"
    stty -echo 2>/dev/null || true; read -r value || value=""; stty echo 2>/dev/null || true; echo
    [ -n "$value" ] && printf '%s=%s\n' "$name" "$value" >> "$keys"
done

"$home/venv/bin/python" "$here/scripts/server.py" --check
echo
here_abs=$(CDPATH= cd "${0%/*}" && pwd)
echo "Codex: add to ~/.codex/config.toml"
echo "  [mcp_servers.marketops]"
echo "  command = \"sh\""
echo "  args = [\"$here_abs/scripts/run.sh\"]"
echo "WorkBuddy: an MCP server of type stdio, command sh, argument $here_abs/scripts/run.sh (see HOSTS.md)."
echo "Done. In Claude Code:  /plugin marketplace add ProfessorZhi/marketops"
echo "                       /plugin install marketops-research-assistant@marketops"
echo "then restart Claude Code and ask for a research task (or /marketops-research-assistant:research ...)."
